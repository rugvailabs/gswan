"""Monitor: detects hung / looping / forgotten workers without an LLM and climbs an escalation ladder.

Detection (cheap, deterministic):
- hung     : a turn is running but no stream-json line for `stall_s` seconds
- looping  : `loop_tools` identical consecutive tool calls, or the worker's last `loop_posts` board posts are identical
- forgot   : worker idle, task still `open`, nothing queued, quiet for `idle_s` (ended its turn without done/blocked)
Ladder (per worker, `grace_s` between rungs):
- hung/looping: nudge event -> kill the turn + resume with a restart prompt (max_restarts) -> escalate to the human
- forgot      : wake with a reminder (max_nudges) -> escalate
Progress (fresh activity after the last action, or a done/verify) resets the ladder.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Callable
from datetime import datetime, timezone

from .gate import Gate
from .models import EventIn, Worker
from .runner import kill_tree
from .tasks import Tasks
from .workers import WorkerError


@dataclass
class Thresholds:
    stall_s: float = 300
    loop_tools: int = 8
    loop_posts: int = 3
    idle_s: float = 120
    grace_s: float = 60
    max_nudges: int = 2
    max_restarts: int = 1
    max_judgements: int = 2  # optional LLM "is it progressing?" checks per hung episode


@dataclass
class Finding:
    worker: str
    state: str  # healthy | hung | looping | forgot | waiting
    detail: str
    action: str = "none"  # none | nudge | restart | remind | escalate


def _ts(s: str | None) -> datetime | None:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc) if s else None


def _norm(body: str) -> str:
    return re.sub(r"\d+", "#", body.strip().lower())[:200]


Judge = Callable[[Worker, list, float], "tuple[bool, str] | None"]  # (worker, recent board events, quiet seconds)


def build_judge_prompt(w: Worker, events: list, quiet_s: float) -> str:
    tools = "\n".join(f"- {t}" for t in w.recent_tools) or "- (none recorded)"
    posts = "\n".join(f"- {e.agent}/{e.kind}: {e.body[:160]}" for e in events[-5:]) or "- (none)"
    return (f"A coding agent ('{w.name}', goal: {w.goal[:200]}) has produced no output for {int(quiet_s)} seconds while running a command.\n"
            f"Its last tool calls:\n{tools}\nRecent board posts:\n{posts}\n\n"
            "Is it plausibly making progress (e.g. a long build, test run, install or download that legitimately takes minutes), "
            "or is it hung (e.g. waiting for input, sleeping, a deadlocked or never-ending command)? "
            'Reply with ONLY a JSON object: {"progress": true|false, "reason": "<one sentence>"}')


def parse_judge_reply(text: str) -> "tuple[bool, str] | None":
    import json
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        return bool(d["progress"]), str(d.get("reason", ""))[:200]
    except (ValueError, KeyError, TypeError):
        return None


def claude_judge(claude_bin: str = "claude", model: str = "haiku", timeout: float = 90) -> Judge:
    """Cheap second opinion from a small model before the monitor kills a possibly-busy worker. No tools, hard $ cap."""
    import json
    import subprocess

    def judge(w: Worker, events: list, quiet_s: float):
        try:
            p = subprocess.run([claude_bin, "-p", build_judge_prompt(w, events, quiet_s), "--model", model, "--output-format", "json",
                                "--tools", "", "--max-budget-usd", "0.05", "--permission-mode", "dontAsk"],
                               capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
            return parse_judge_reply(json.loads(p.stdout).get("result", ""))
        except Exception:
            return None  # a failed judge never blocks the ladder
    return judge


class Monitor:
    def __init__(self, gate: Gate, th: Thresholds | None = None, judge: Judge | None = None):
        self.judge = judge
        self.gate, self.runner, self.board = gate, gate.runner, gate.board
        self.reg, self.tasks, self.th = self.runner.reg, Tasks(gate.board), th or Thresholds()
        self._threads: list[threading.Thread] = []
        self._dispatching: set[str] = set()
        self._stop = threading.Event()

    # ---- detection -----------------------------------------------------------------
    def diagnose(self, w: Worker, now: datetime) -> Finding:
        t = self.th
        last = _ts(w.last_activity)
        quiet = (now - last).total_seconds() if last else None
        if w.status == "running":
            if quiet is not None and quiet > t.stall_s:
                return Finding(w.name, "hung", f"no output for {int(quiet)}s (last: {w.activity_note or '?'})")
            if w.tool_repeat >= t.loop_tools:
                return Finding(w.name, "looping", f"{w.tool_repeat} identical consecutive tool calls ({w.activity_note})")
            mine = [e for e in self.board.list_events(agent=w.name, limit=500).items if e.kind in ("update", "claim", "blocked")][-t.loop_posts:]
            if len(mine) == t.loop_posts and len({_norm(e.body) for e in mine}) == 1:
                return Finding(w.name, "looping", f"last {t.loop_posts} board posts are identical: {mine[-1].body[:80]}")
            return Finding(w.name, "healthy", f"working ({w.activity_note or 'starting'})")
        task = self.tasks.get(w.name)
        mine = self.board.list_events(agent=w.name, limit=500).items
        if mine and mine[-1].kind == "blocked" and task.status == "open":
            return Finding(w.name, "blocked", f"reported blocked: {mine[-1].body[:100]}")  # coordinator's call, not a stall
        if task.status == "open" and self.reg.queue_len(w) == 0 and w.status == "idle" and w.turns > 0:
            ref = last or _ts(w.updated_at)
            if ref and (now - ref).total_seconds() > t.idle_s:
                return Finding(w.name, "forgot", "turn ended without `done` or `blocked`")
        if task.status in ("awaiting_verification", "verifying"):
            return Finding(w.name, "waiting", f"task {task.status}")
        return Finding(w.name, "healthy", f"idle, task {task.status}")

    def check(self, now: datetime | None = None) -> list[Finding]:
        now = now or datetime.now(timezone.utc)
        return [self.diagnose(w, now) for w in self.reg.list()]

    # ---- ladder ----------------------------------------------------------------------
    def step(self, now: datetime | None = None) -> list[Finding]:
        """Diagnose and act once. Returns findings with `action` filled in."""
        now = now or datetime.now(timezone.utc)
        self.dispatch()
        out = []
        for f in self.check(now):
            st = self.reg.mon_get(f.worker)
            w = self.reg.get(f.worker)
            if f.state in ("healthy", "waiting", "blocked"):
                if (st["nudges"] or st["restarts"]) and (_ts(w.last_activity) or now) > (_ts(st["last_action"]) or now):
                    self.reg.mon_set(f.worker, nudges=0, restarts=0, last_action=None)  # progress resets the ladder
                out.append(f); continue
            since = (now - _ts(st["last_action"])).total_seconds() if st["last_action"] else None
            if since is not None and since < self.th.grace_s:
                out.append(f); continue  # give the last action time to work
            stamp = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            if f.state == "hung" and self.judge and st["judged"] < self.th.max_judgements:
                quiet = (now - (_ts(w.last_activity) or now)).total_seconds()
                verdict = self.judge(w, self.board.list_events(limit=500).items, quiet)
                if verdict and verdict[0]:  # a small model thinks it is legitimately busy: extend, do not climb the ladder
                    self._post("update", f"{w.name} quiet for {int(quiet)}s but judged still progressing ({verdict[1]}); extending")
                    self.reg.mon_set(f.worker, nudges=st["nudges"], restarts=st["restarts"], last_action=stamp, judged=st["judged"] + 1)
                    f.action = "extend"; out.append(f); continue
            if f.state in ("hung", "looping"):
                if st["nudges"] == 0:
                    self._post("nudge", f"{f.worker} {f.state}: {f.detail}. If it does not recover it will be restarted.")
                    self.reg.mon_set(f.worker, nudges=1, restarts=st["restarts"], last_action=stamp); f.action = "nudge"
                elif st["restarts"] < self.th.max_restarts:
                    self._restart(w, f)
                    self.reg.mon_set(f.worker, nudges=st["nudges"], restarts=st["restarts"] + 1, last_action=stamp); f.action = "restart"
                else:
                    self._escalate(w, f); f.action = "escalate"
            elif f.state == "forgot":
                if st["nudges"] < self.th.max_nudges:
                    self._spawn(w.name, "You ended your turn without posting `done` or `blocked`. Check `git status`, finish or "
                                        "report what is blocking you, then post `done` (what changed, how verified) or `blocked`.")
                    self._post("nudge", f"{f.worker} forgot to report; reminded ({st['nudges'] + 1}/{self.th.max_nudges}).")
                    self.reg.mon_set(f.worker, nudges=st["nudges"] + 1, restarts=st["restarts"], last_action=stamp); f.action = "remind"
                else:
                    self._escalate(w, f); f.action = "escalate"
            out.append(f)
        return out

    def dispatch(self) -> list[str]:
        """Wake idle workers that have queued (peer) mentions. Skipped silently when the budget is exhausted."""
        started = []
        for name in self.runner.pending():
            if name in self._dispatching:
                continue
            try:
                self.runner.limits.check_turn()
            except Exception:
                continue  # run_queue posts the (deduplicated) budget warning when someone tries
            self._dispatching.add(name)

            def run(n: str = name) -> None:
                try:
                    self.gate.drain(n)
                except Exception as e:
                    self._post("blocked", f"monitor could not run queued messages for {n}: {e}")
                finally:
                    self._dispatching.discard(n)
            th = threading.Thread(target=run, name=f"dispatch-{name}", daemon=True)
            th.start(); self._threads.append(th); started.append(name)
        return started

    def _post(self, kind: str, body: str) -> None:
        self.board.post(EventIn(agent="monitor", kind=kind, body=body[:2000]))

    def _escalate(self, w: Worker, f: Finding) -> None:
        self.tasks.set_status(w.name, "escalated")
        self._post("blocked", f"{w.name} {f.state} and the monitor ladder is exhausted: {f.detail}. Needs a human.")

    def _restart(self, w: Worker, f: Finding) -> None:
        self._post("nudge", f"restarting {w.name} ({f.state}: {f.detail})")
        self.reg.touch(w.name, "terminated by monitor")  # lets the runner tell a kill from a real failure
        if w.child_pid:
            kill_tree(w.child_pid)
        msg = (f"Your previous turn was terminated by the monitor ({f.state}: {f.detail}). Your session and worktree are intact. "
               f"Run `git status`, then continue the goal with a DIFFERENT approach than the one that stalled. Board digest:\n"
               + self.board.digest().text)
        self._spawn(w.name, msg, wait_idle=True)

    def _spawn(self, name: str, message: str, wait_idle: bool = False) -> None:
        def run() -> None:
            if wait_idle:  # let the killed turn unwind and release the worker
                for _ in range(100):
                    if self.reg.get(name).status != "running":
                        break
                    time.sleep(0.1)
            try:
                self.gate.invoke(name, message, sender="monitor")
            except Exception as e:  # never let the monitor thread die
                self._post("blocked", f"monitor could not wake {name}: {e}")
        th = threading.Thread(target=run, name=f"monitor-wake-{name}", daemon=True)
        th.start(); self._threads.append(th)

    def join(self, timeout: float = 30) -> None:
        for th in self._threads:
            th.join(timeout)

    # ---- background loop -----------------------------------------------------------------
    def start(self, interval: float = 15) -> threading.Thread:
        def loop() -> None:
            while not self._stop.wait(interval):
                try:
                    self.step()
                except Exception:
                    pass  # no active swarm yet, or transient db error
        th = threading.Thread(target=loop, name="swarm-monitor", daemon=True)
        th.start()
        return th

    def stop(self) -> None:
        self._stop.set()
