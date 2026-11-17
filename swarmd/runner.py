"""Runs workers as persistent headless `claude -p` sessions, one git worktree each.

Idle worker == no process. `@mention` == `claude -p --resume <session_id>`.
One turn at a time per worker (spike: concurrent resumes of one session lose a turn);
extra mentions are queued in SQLite and merged into the next turn.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .board import Board
from .models import EventIn, TurnResult, Worker, WorkerSpec
from .limits import BudgetExceeded, LimitsStore
from .workers import Registry, WorkerError

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # where the swarmd package lives


class RunnerError(Exception):
    pass


@dataclass
class Exec:
    returncode: int
    stdout: str
    stderr: str


Executor = Callable[[list[str], Path, dict[str, str], float], Exec]


def subprocess_executor(cmd: list[str], cwd: Path, env: dict[str, str], timeout: float) -> Exec:
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return Exec(p.returncode, p.stdout, p.stderr)
    except subprocess.TimeoutExpired:
        return Exec(124, "", f"turn timed out after {timeout}s")


class StreamingExecutor:
    """Runs `claude -p ... --output-format stream-json --verbose`, recording activity per stream line so the
    monitor can tell a hung or looping turn from a busy one. Returns the final `result` line as stdout."""

    def __init__(self, board: Board):
        self.reg = Registry(board)

    def __call__(self, cmd: list[str], cwd: Path, env: dict[str, str], timeout: float) -> Exec:
        name = env.get("SWARM_WORKER", "")
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, start_new_session=True)
        self._safe(self.reg.set_child_pid, name, p.pid)
        err: list[str] = []
        t_err = threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True); t_err.start()
        timed_out = threading.Event()

        def kill_on_timeout() -> None:
            timed_out.set(); kill_tree(p.pid)
        timer = threading.Timer(timeout, kill_on_timeout); timer.start()
        result_line = ""
        last_t, max_gap = time.monotonic(), 0.0
        try:
            for line in p.stdout:
                now_t = time.monotonic(); max_gap = max(max_gap, now_t - last_t); last_t = now_t
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") == "result":
                    result_line = line
                    continue
                note, sig = None, None
                if d.get("type") == "assistant":
                    for b in (d.get("message") or {}).get("content") or []:
                        if b.get("type") == "tool_use":
                            note = f"tool:{b.get('name')}"
                            sig = b.get("name", "") + json.dumps(b.get("input"), sort_keys=True)[:300]
                            inp = b.get("input") or {}
                            detail = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                            self._safe(self.reg.record_tool, name, f"{b.get('name')}: {detail}")
                self._safe(self.reg.touch, name, note, sig)
            p.wait()
        finally:
            timer.cancel(); self._safe(self.reg.set_child_pid, name, None)
            self._safe(self.reg.bump_gap, name, max(max_gap, time.monotonic() - last_t))
        t_err.join(timeout=2)
        stderr = "".join(err)
        if timed_out.is_set():
            return Exec(124, result_line, f"turn timed out after {timeout}s")
        if p.returncode and p.returncode < 0:
            stderr = f"terminated by signal {-p.returncode} (monitor or user)\n" + stderr
        return Exec(p.returncode, result_line, stderr)

    @staticmethod
    def _safe(fn, *a) -> None:
        try:
            fn(*a)
        except Exception:  # activity bookkeeping must never break a turn
            pass


def kill_tree(pid: int, grace: float = 3.0) -> None:
    """SIGTERM the process group (claude + its tool children), SIGKILL if it lingers."""
    import signal, time
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    end = time.time() + grace
    while time.time() < end:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


class Runner:
    def __init__(self, board: Board, root: Path, *, executor: Executor | None = None,
                 claude_bin: str | None = None, turn_timeout: float = 900, budget_per_turn: float | None = 2.0):
        self.board, self.root = board, Path(root).resolve()
        self.reg = Registry(board)
        self.limits = LimitsStore(board)
        self.exec = executor or StreamingExecutor(board)
        self.claude_bin = claude_bin or os.environ.get("SWARM_CLAUDE_BIN", "claude")  # override for tests/other installs
        self.timeout, self.budget = turn_timeout, budget_per_turn
        self.cli = f"{sys.executable} -m swarmd.cli"

    # ---- setup ------------------------------------------------------------
    def define(self, spec: WorkerSpec) -> Worker:
        w = self.reg.define(spec)
        self.board.post(EventIn(agent="coordinator", kind="plan",
                                body=f"worker {w.name}: {w.goal} | owns {', '.join(w.owns)}"))
        return w

    def ensure_worktree(self, w: Worker) -> Path:
        if w.worktree and Path(w.worktree).is_dir():
            return Path(w.worktree)
        if _git(self.root, "rev-parse", "--verify", "HEAD").returncode != 0:
            raise RunnerError(f"{self.root} has no commits yet; worktrees need an initial commit (git commit --allow-empty -m init)")
        if w.role == "verifier":
            if not (w.cwd and Path(w.cwd).is_dir()):
                raise RunnerError(f"verifier {w.name} has no worktree to inspect (target never ran)")
            return Path(w.cwd)
        base = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        path = self.root / ".swarm" / "wt" / w.name
        branch = f"swarm/{w.swarm_id}/{w.name}"
        r = _git(self.root, "worktree", "add", "-q", str(path), "-b", branch)
        if r.returncode != 0:
            raise RunnerError(f"git worktree add failed: {r.stderr.strip()}")
        self.reg.set_worktree(w, str(path), branch)
        self.reg.set_base(w, base)
        self._link_dependencies(path)
        return path

    def _link_dependencies(self, path: Path) -> None:
        """Untracked dependency folders (node_modules) are absent from a fresh worktree, so tests could not run there.
        Symlink the project's installed copy; git ignores it only if the project's .gitignore does (node_modules always is)."""
        src = self.root / "node_modules"
        dst = path / "node_modules"
        if src.is_dir() and not dst.exists() and _git(self.root, "check-ignore", "-q", "node_modules").returncode == 0:
            try:
                dst.symlink_to(src, target_is_directory=True)
            except OSError:
                pass

    def settings_path(self) -> Path:
        p = self.root / ".swarm" / "hooks.json"
        cfg = {"hooks": {"PreToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash", "hooks": [
            {"type": "command", "command": f"{sys.executable} -m swarmd.guard"}]}]}}
        text = json.dumps(cfg, indent=2)
        if not p.exists() or p.read_text() != text:
            p.write_text(text)
        return p

    # ---- command construction (pure; unit-tested) ---------------------------
    def verifier_charter(self, w: Worker) -> str:
        return (
            f"You are the swarm verifier '{w.name}'. You independently check one worker's task and give a verdict. You CANNOT edit files. "
            f"Your working directory is the worker's worktree. Do not trust the worker's summary: read the actual changes "
            f"(git diff <base>, git log, Read) and run the allowed test commands if any. Check the goal literally, ownership "
            f"(changes only inside the owned paths) and that claims are backed by evidence. Fail on: unmet goal, failing or "
            f"missing tests the goal needs, edits outside owned paths, unrelated changes, claims you cannot confirm. "
            f"Give the verdict exactly once with the board CLI: `{self.cli} verdict <worker> pass --reason \"<evidence>\"` or "
            f"`{self.cli} verdict <worker> fail --reason \"<specific reproducible problem>\"` (repeat --reason for several). "
            f"Use plain single commands (no cd, &&, pipes). If you could not run a check, say so in the reason."
        )

    def charter(self, w: Worker) -> str:
        if w.role == "verifier":
            return self.verifier_charter(w)
        return (
            f"You are swarm worker '{w.name}'. Goal: {w.goal}\n"
            f"You may only write inside: {', '.join(w.owns)} (relative to your working directory, a private git worktree). "
            f"Writes elsewhere are blocked; if you need a change elsewhere, post a 'blocked' event and stop.\n"
            f"Message board CLI: `{self.cli}`. Post progress with: {self.cli} post {w.name} <claim|update|blocked|done> \"<message>\". "
            f"First `claim`, then short `update`s at milestones, and `done` when finished (what changed, how you verified, what is unfinished). "
            f"Read the board with `{self.cli} digest`. Commit your work in your worktree branch before posting done. "
            f"{self.teammates(w)}"
            f"Shell commands go through a policy: the board CLI, git (status/diff/log/add/commit), mkdir/touch inside your worktree, "
            f"read-only tools (ls, cat, grep, echo...) and ONLY the test/run commands granted to you ({', '.join(w.tools) or 'none'}). "
            f"Env prefixes like PYTHONPATH=src (or `env PYTHONPATH=src ...`), `cd <subdir> &&`, `;` and pipes between allowed commands are fine; redirections (>), $(...), backticks and background jobs are not, which also applies inside board messages (no backticks or $( in text you post). "
            f"Use `python3` (not an absolute interpreter path) for your own checks. To use a finished teammate's code, run `git merge swarm/{w.swarm_id}/<name>` once the coordinator says it is done. "
            f"If a command is denied, say so instead of claiming it ran. "
            f"Never commit build artifacts (__pycache__, *.pyc, node_modules, .venv). Be concise. Never claim success you did not verify."
        )

    def teammates(self, w: Worker) -> str:
        try:
            others = [o for o in self.reg.list() if o.name != w.name]
        except Exception:
            others = []
        if not others:
            return ""
        lines = "; ".join(f"{o.name} (owns {', '.join(o.owns)}: {o.goal[:80]})" for o in others)
        return (f"Teammates: {lines}. To ask one for something you need, run `{self.cli} mention <worker> \"<message>\"` "
                f"(it is woken within ~15s; do NOT wait for the reply, keep working or post `blocked`). Be specific; max {self.MAX_PEER_MESSAGES} messages per teammate. ")

    def allowed_tools(self, w: Worker) -> list[str]:
        """File tools only. Every Bash call is decided by the PreToolUse policy hook (guard.py/bashpolicy.py), which also
        understands env prefixes, `cd` inside the worktree, `&&`/`;`/pipes, mkdir and safe git; granted commands come from
        `w.tools` via SWARM_ALLOW."""
        if w.role == "verifier":
            return ["Read", "Glob", "Grep"]
        return ["Read", "Glob", "Grep", "Write", "Edit"]

    def build_command(self, w: Worker, prompt: str, *, first_turn: bool) -> list[str]:
        cmd = [self.claude_bin, "-p", prompt, "--model", w.model, "--output-format", "stream-json", "--verbose",
               "--permission-mode", "dontAsk", "--settings", str(self.settings_path()),
               "--append-system-prompt", self.charter(w)]
        if self.budget:
            cmd += ["--max-budget-usd", str(self.budget)]
        cmd += ["--allowedTools", *self.allowed_tools(w)]
        cmd += ["--session-id" if first_turn else "--resume", w.session_id]
        return cmd

    def build_env(self, w: Worker, wt: Path) -> dict[str, str]:
        env = dict(os.environ)
        env.update(SWARM_HOME=str(self.root), SWARM_WORKER=w.name, PYTHONPATH=str(PROJECT_ROOT), SWARM_ROLE=w.role,
                   PYTHONDONTWRITEBYTECODE="1", SWARM_WORKTREE=str(wt), SWARM_ALLOW=json.dumps(w.tools), SWARM_PYTHON=sys.executable,
                   SWARM_BRANCH_PREFIX=f"swarm/{w.swarm_id}/",
                   SWARM_OWNS=os.pathsep.join(str((wt / o).resolve()) for o in w.owns))
        return env

    @staticmethod
    def merge_messages(msgs: list[tuple[str, str]]) -> str:
        if len(msgs) == 1:
            return msgs[0][1]
        return "\n\n".join(f"[{s}] {m}" for s, m in msgs)

    # ---- one turn ---------------------------------------------------------
    def _run_turn(self, w: Worker, prompt: str, wt: Path, first: bool) -> tuple[bool, str, float, list[str], str]:
        for attempt in (0, 1):
            self.reg.touch(w.name, "turn start", reset=True)
            ex = self.exec(self.build_command(w, prompt, first_turn=first), wt, self.build_env(w, wt), self.timeout)
            err = (ex.stderr or "")[:500]
            if first and attempt == 0 and "already in use" in (ex.stderr + ex.stdout):
                first = False  # session exists (earlier turn crashed after creation): resume instead
                continue
            break
        try:
            d = json.loads(ex.stdout)
        except json.JSONDecodeError:
            return False, "", 0.0, [], err or f"exit {ex.returncode}, unparseable output: {ex.stdout[:200]}"
        denials = []
        for x in d.get("permission_denials", []):
            ti = x.get("tool_input") or {}
            detail = ti.get("command") or ti.get("file_path") or ""
            denials.append(f"{x.get('tool_name', '?')}({detail[:100]})" if detail else x.get("tool_name", "?"))
        ok = ex.returncode == 0 and not d.get("is_error")
        return ok, str(d.get("result", "")), float(d.get("total_cost_usd") or 0), denials, "" if ok else (str(d.get("result"))[:300] or err)

    MAX_PEER_MESSAGES = 5  # per sender->target pair per swarm: stops worker ping-pong

    def enqueue_mention(self, sender: str, target: str, message: str) -> dict:
        """Worker-to-worker @mention: post + queue only. The monitor's dispatcher wakes the target (never inline,
        so the sender's turn is not blocked)."""
        if sender == target:
            return {"ok": False, "error": "a worker cannot mention itself"}
        try:
            snd, tgt = self.reg.get(sender), self.reg.get(target)
        except WorkerError as e:
            return {"ok": False, "error": str(e)}
        if snd.role != "worker" or tgt.role != "worker":
            return {"ok": False, "error": "only workers can mention workers"}
        prior = [e for e in self.board.list_events(agent=sender, kind="mention", limit=500).items if e.body.startswith(f"@{target} ")]
        if len(prior) >= self.MAX_PEER_MESSAGES:
            return {"ok": False, "error": f"{sender} already sent {len(prior)} messages to {target}; post `blocked` for the coordinator instead"}
        self.board.post(EventIn(agent=sender, kind="mention", body=f"@{target} {message}"[:8000]))
        self.reg.enqueue(tgt, sender, message)
        return {"ok": True, "queued": target}

    def pending(self) -> list[str]:
        """Workers that have queued messages but no live runner (e.g. after a peer mention)."""
        from .workers import pid_alive
        out = []
        for w in self.reg.list():
            if self.reg.queue_len(w) and not (w.status == "running" and pid_alive(w.pid)):
                out.append(w.name)
        return out

    def invoke(self, name: str, message: str, sender: str = "coordinator") -> TurnResult:
        """@mention a worker: post + queue the message, then run turns until the queue is empty.
        If another process is already running this worker, the message stays queued for it."""
        w = self.reg.get(name)
        self.board.post(EventIn(agent=sender, kind="mention", body=f"@{name} {message}"[:8000]))
        self.reg.enqueue(w, sender, message)
        res = self.run_queue(name)
        return res or TurnResult(worker=name, ok=True, text="queued", session_id=w.session_id, queued_only=True)

    def run_queue(self, name: str) -> TurnResult | None:
        """Claim the worker and process its queue. None if someone else holds it (they will drain it)."""
        last: TurnResult | None = None
        while True:
            w = self.reg.get(name)
            if not self.reg.queue_len(w) or not self.reg.try_claim(w):
                return last
            had_error = False
            try:
                wt = self.ensure_worktree(w)
                while True:
                    w = self.reg.get(name)
                    if self.reg.queue_len(w):
                        self.limits.check_turn()  # raises BudgetExceeded; queued messages stay queued
                    msgs = self.reg.drain(w)
                    if not msgs:
                        break
                    ok, text, cost, denials, err = self._run_turn(w, self.merge_messages(msgs), wt, first=w.turns == 0)
                    self.reg.finish_turn(w, ok=ok, cost=cost, error=err)
                    last = TurnResult(worker=name, ok=ok, text=text or err, session_id=w.session_id, cost_usd=cost,
                                      denials=denials, turns_run=(last.turns_run if last else 0) + 1)
                    if not ok:
                        had_error = True
                        killed = "terminated by signal" in err or self.reg.get(name).activity_note == "terminated by monitor"
                        self.board.post(EventIn(agent="swarmd", kind="update" if killed else "blocked",
                                                body=(f"worker {name} turn was terminated (monitor/user); it will be resumed" if killed
                                                      else f"worker {name} turn failed: {err}")[:2000]))
                        break
                    if denials:
                        self.board.post(EventIn(agent="swarmd", kind="update",
                                                body=f"worker {name} was denied: {'; '.join(sorted(set(denials)))}"[:2000]))
            except BudgetExceeded as e:
                had_error = True
                self.board.post_once(EventIn(agent="swarmd", kind="blocked", body=str(e)[:2000]))
                last = TurnResult(worker=name, ok=False, text=str(e), session_id=w.session_id,
                                  turns_run=last.turns_run if last else 0)
            except (RunnerError, WorkerError) as e:
                had_error = True
                self.board.post(EventIn(agent="swarmd", kind="blocked", body=f"worker {name}: {e}"[:2000]))
                last = TurnResult(worker=name, ok=False, text=str(e), session_id=w.session_id,
                                  turns_run=last.turns_run if last else 0)
            finally:
                self.reg.release(self.reg.get(name), error=had_error)
            if had_error:
                return last
            # loop again: a mention may have been queued after our last drain but before release
