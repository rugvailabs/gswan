"""MCP server (stdio) exposing the swarm to Claude Code agents.

Coordinator (default): swarm_start, define_worker, invoke_worker, wait_for_workers, record_decision, swarm_status, board_post, board_digest.
Workers (SWARM_ROLE=worker): board_post, board_digest only.
`invoke_worker` is non-blocking (a turn can take minutes); use `wait_for_workers` to block with a bounded timeout.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import decisions, tokens
from .approvals import ApprovalError, Approvals
from .board import Board, NoActiveSwarm
from .limits import BudgetExceeded
from .cli import ensure_swarm_dir, find_root, open_board
from .gate import Gate
from .integrate import IntegrationError, Integrator
from .models import EVENT_KINDS, EventIn, WorkerSpec
from .monitor import Monitor
from .runner import Runner, RunnerError
from .tasks import Tasks
from .workers import WorkerError

MAX_WAIT = 110.0  # stay under typical MCP tool timeouts
SUMMARY_CHARS = 1500


class SwarmTools:
    def __init__(self, board: Board, runner: Runner, gate: Gate | None = None):
        self.board, self.runner = board, runner
        self.gate = gate or Gate(runner)
        self.tasks = Tasks(board)
        self.monitor = Monitor(self.gate)
        self.integrator = Integrator(self.gate)
        self.threads: dict[str, threading.Thread] = {}
        self.last: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.cursor = 0  # board seq already reported by wait_for_workers

    # ---- helpers ------------------------------------------------------------
    def _err(self, e: Exception) -> dict:
        return {"ok": False, "error": str(e)}

    def budget_block(self) -> str | None:
        """Why no new turn can start right now (a budget is used up), or None."""
        try:
            self.runner.limits.check_turn()
        except BudgetExceeded as e:
            return str(e)
        except NoActiveSwarm:
            return None
        return None

    def _busy(self, name: str) -> bool:
        t = self.threads.get(name)
        if t and t.is_alive():
            return True
        stopped = self.budget_block() is not None  # queued work cannot run until a human raises the budget: paused, not busy
        for n in (name, f"verify-{name}"):
            try:
                w = self.runner.reg.get(n)
            except (WorkerError, NoActiveSwarm):
                continue
            if w.status == "running" or (self.runner.reg.queue_len(w) > 0 and not stopped):
                return True
        return False

    def paused_view(self) -> dict:
        why, held = self.budget_block(), [p["worker"] for p in self.gate.paused()]
        queued = self.runner.pending() if why else []  # messages that cannot run until the budget is raised
        agents = sorted(set(held) | set(queued))
        return {"paused": bool(agents), "reason": why, "agents": agents}

    def resume(self) -> list[str]:
        """After a human raises a budget: run queued messages and paused verifications again."""
        if self.budget_block():
            return []
        started = self.gate.resume()
        for n in self.runner.pending():
            if not (self.threads.get(n) and self.threads[n].is_alive()):
                th = threading.Thread(target=self.gate.drain, args=(n,), name=f"resume-{n}", daemon=True)
                self.threads[n] = th; th.start(); started.append(n)
        return started

    # ---- approvals (human-only: HTTP API / CLI; deliberately not an MCP tool) ---------------------------------
    def list_approvals(self, status: str | None = "pending") -> dict:
        try:
            return {"ok": True, "approvals": [a.view() for a in Approvals(self.board).list(status or None)]}
        except NoActiveSwarm as e:
            return self._err(e)

    def decide_approval(self, aid: str, decision: str, note: str = "") -> dict:
        try:
            a = Approvals(self.board).decide(aid, decision, note)
        except (ApprovalError, NoActiveSwarm) as e:
            return self._err(e)
        self.invoke_worker(a.worker, Approvals.message(a), sender="human")  # wakes it, or queues behind its running turn
        return {"ok": True, "approval": a.view()}

    def _worker_view(self, w) -> dict:
        try:
            t = self.tasks.get(w.name)
            task = {"id": t.id, "status": t.status, "failedRounds": t.rounds, "reasons": t.reasons}
        except Exception:
            task = None
        return {"task": task, "name": w.name, "status": w.status, "busy": self._busy(w.name), "turns": w.turns,
                "tokens": tokens.worker_view(w), "owns": w.owns, "branch": w.branch, "model": w.model, "queue": self.runner.reg.queue_len(w),
                "recentTools": w.recent_tools, "checks": w.checks, "group": w.group, "lastActivity": w.last_activity, "activityNote": w.activity_note,
                "worktree": w.worktree, "lastError": w.last_error, "lastResult": self.last.get(w.name)}

    # ---- coordinator tools ----------------------------------------------------
    def swarm_start(self, task: str, max_workers: int | None = None, max_wakeups: int | None = None,
                    max_wall_s: int | None = None, max_tokens: int | None = None, critics: bool = False) -> dict:
        """Start a swarm. Budgets default to 6 workers / 40 wake-ups / 3600s / 3M tokens (input + output); only a human can raise them later."""
        sw = self.board.start_swarm(task, limits={"max_workers": max_workers, "max_wakeups": max_wakeups,
                                                   "max_wall_s": max_wall_s, "max_tokens": max_tokens})
        self.threads.clear(); self.last.clear(); self.cursor = 0
        self.gate.critic_model = os.environ.get("SWARM_CRITIC_MODEL") or ("sonnet" if critics else None)  # per-worker diff critics before the verifier
        return {"ok": True, "swarmId": sw.id, "task": sw.task, **self.runner.limits.snapshot()}

    def define_worker(self, name: str, goal: str, owns: list[str], tools: list[str] | None = None,
                      model: str = "sonnet", checks: list[str] | None = None, group: str | None = None) -> dict:
        from . import bashpolicy
        try:
            spec = WorkerSpec(name=name, goal=goal, owns=owns, tools=tools or [], model=model, checks=checks or [], group=group or None)
            for c in spec.checks:  # a check the verifier is not allowed to run would make every pass impossible
                try:
                    bashpolicy.check(c, role="verifier", worktree=str(self.runner.root), cwd=None, grants=spec.tools,
                                     cli_python=None, branch_prefix=None, env={})
                except bashpolicy.Denied as d:
                    raise ValueError(f"check `{c}` could not run under the verifier's command policy ({d}); grant it in tools, e.g. 'Bash({c.split()[0]}:*)'")
            w = self.runner.define(spec)
        except (ValueError, WorkerError, NoActiveSwarm) as e:
            return self._err(e)
        return {"ok": True, "worker": w.name, "id": w.id, "owns": w.owns, "checks": w.checks, "group": w.group}

    def critique_plan(self, plan: str, model: str = "sonnet") -> dict:
        """Start a read-only plan critic on your plan BEFORE invoking workers. Findings arrive as `critique` events on target `plan`."""
        from .critics import Critiques
        try:
            c = self.runner.reg.define_critic("plan-critic", "plan: missing requirements, overlapping ownership, risky assumptions, untestable goals", model=model)
            n = Critiques(self.board).begin_round("plan")
        except (ValueError, WorkerError, NoActiveSwarm) as e:
            return self._err(e)
        ws = "; ".join(f"{w.name} (owns {', '.join(w.owns)}): {w.goal}" for w in self.runner.reg.list()) or "(none defined yet)"
        brief = (f"Critique this plan before work starts. Target for your findings: plan (round {n}). No file:line needed.\n"
                 f"Task: {self.board._require().task}\nPlan:\n{plan}\nDefined workers: {ws}\n"
                 f"Check: requirements missing, workers whose owned paths overlap, assumptions about the repo you can verify by reading it, "
                 f"goals that cannot be verified, interfaces between workers left undefined.")
        return self.invoke_worker(c.name, brief, sender="coordinator")

    def define_researcher(self, name: str, question: str, model: str = "sonnet") -> dict:
        """Read-only research agent (repo + web). Wake it with invoke_worker; findings arrive as `research` board events."""
        try:
            w = self.runner.define_researcher(name, question, model)
        except (ValueError, WorkerError, NoActiveSwarm) as e:
            return self._err(e)
        return {"ok": True, "researcher": w.name, "id": w.id}

    def invoke_worker(self, name: str, message: str, sender: str = "coordinator") -> dict:
        """@mention a worker, a group, or `all`. Returns immediately; the turns run in the background."""
        try:
            targets = self.runner.reg.resolve(name)
        except (WorkerError, NoActiveSwarm) as e:
            return self._err(e)
        if len(targets) > 1 or targets[0].name != name:  # a group: one board event, one turn per member
            self.board.post(EventIn(agent=sender, kind="mention", body=f"@{name} {message}"[:8000], refs=[f"to:{t.name}" for t in targets]))
            started = [t.name for t in targets if self._start(t.name, message, sender, announce=False)]
            return {"ok": True, "started": started, "group": name}
        self._start(name, message, sender)
        return {"ok": True, "started": name}

    def _start(self, name: str, message: str, sender: str, announce: bool = True) -> bool:
        def run() -> None:
            try:
                res = self.gate.invoke(name, message, sender=sender, announce=announce)
                self.last[name] = {"ok": res.ok, "text": res.text[:SUMMARY_CHARS], "tokens": res.tokens,
                                   "denials": res.denials, "turns": res.turns_run, "queuedOnly": res.queued_only}
            except (RunnerError, WorkerError, NoActiveSwarm, ValueError) as e:
                self.last[name] = {"ok": False, "text": str(e)}

        with self.lock:
            t = threading.Thread(target=run, name=f"worker-{name}", daemon=True)
            self.threads[name] = t
            t.start()
        return True

    def set_worker_model(self, worker: str, model: str, why: str = "", by: str = "coordinator") -> dict:
        """Move an idle worker to another model; it keeps its conversation. The reason is recorded as a decision."""
        why = why.strip()
        if by != "human" and len(why) < decisions.MIN_WHY:
            return self._err(ValueError(f"say why you are changing the model (at least {decisions.MIN_WHY} characters), e.g. 'parser is simple, haiku is enough'"))
        try:
            old, w = self.runner.reg.set_model(worker, model)
        except (WorkerError, NoActiveSwarm) as e:
            return self._err(e)
        if old != model:
            decisions.record(self.board, "other", f"{worker} model {old} -> {model}: {why or 'changed by a human from the dashboard or CLI'}",
                             agent="human" if by == "human" else "coordinator")
        return {"ok": True, "worker": worker, "from": old, "to": w.model}

    def wait_for_workers(self, names: list[str] | None = None, timeout_s: float = 90) -> dict:
        """Block until the named workers (default: all) are idle with empty queues, or timeout."""
        timeout = max(1.0, min(float(timeout_s), MAX_WAIT))
        try:
            targets = names or [w.name for w in self.runner.reg.list() + self.runner.reg.list_researchers() + self.runner.reg.list_critics()]
        except NoActiveSwarm as e:
            return self._err(e)
        deadline = time.time() + timeout
        time.sleep(0.3)  # let freshly started threads claim their workers
        while time.time() < deadline and any(self._busy(n) for n in targets):
            time.sleep(0.5)
        views = []
        for n in targets:
            try:
                views.append(self._worker_view(self.runner.reg.get(n)))
            except WorkerError as e:
                views.append({"name": n, "error": str(e)})
        new = self.board.list_events(since_seq=self.cursor, limit=200).items
        if new:
            self.cursor = new[-1].seq
        pause = self.paused_view()
        return {"ok": True, "timedOut": any(self._busy(n) for n in targets), "workers": views,
                **({"paused": pause} if pause["paused"] else {}),
                **({"pendingApprovals": [f"{a.id} {a.worker}: {a.command[:120]}" for a in Approvals(self.board).list("pending")]}
                   if Approvals(self.board).list("pending") else {}),
                "newEvents": [f"#{e.seq} {e.agent}/{e.kind}: {e.body[:300]}" for e in new]}  # events since the previous wait

    def merge_workers(self, auto_resolve: bool = True, order: list[str] | None = None, verify: bool = False) -> dict:
        """Merge every worker whose task is `done` into the integration branch (never your checked-out branch).
        On a conflict an `integrator` worker resolves it (wait_for_workers, then call this again to continue)."""
        try:
            owed = decisions.missing(self.board)
            if owed:  # explain first: the run report is only as honest as the reasons recorded in it
                return {"ok": False, "missingDecisions": [o.view() for o in owed],
                        "error": "record_decision is required before merging: " + decisions.owed_text(owed)}
            self.integrator.last_thread = None
            out = self.integrator.merge_ready(auto_resolve=auto_resolve, order=order)
            if verify and out.get("allMerged") and not out.get("integratorStarted"):
                self.integrator.verify_integration()  # read-only verifier runs the granted test commands on the merged branch
                out["verificationStarted"] = True
            if self.integrator.last_thread is not None:
                self.threads["integrator"] = self.integrator.last_thread
            return out
        except (IntegrationError, WorkerError, NoActiveSwarm) as e:
            return self._err(e)

    def record_decision(self, topic: str, why: str, worker: str = "") -> dict:
        """Record WHY you made a choice (kind=decision). Required: `split`, `escalation <worker>`, `critic <worker|plan>`."""
        try:
            e = decisions.record(self.board, topic, why, worker)
            return {"ok": True, "seq": e.seq, "stillMissing": [o.view() for o in decisions.missing(self.board)]}
        except (decisions.DecisionError, ValueError, NoActiveSwarm) as ex:
            return self._err(ex)

    def set_limits(self, **kw) -> dict:
        """Human-only (HTTP API / CLI), deliberately not an MCP tool: the coordinator cannot raise its own budget."""
        try:
            self.runner.limits.set(**kw)
        except (ValueError, NoActiveSwarm) as e:
            return self._err(e)
        return {"ok": True, **self.runner.limits.snapshot(), "resumed": self.resume()}

    def swarm_status(self) -> dict:
        try:
            ws = [self._worker_view(w) for w in self.runner.reg.list() + self.runner.reg.list_researchers() + self.runner.reg.list_critics() if w.name == "plan-critic" or w.role != "critic"]
            unsettled = [t.worker for t in self.tasks.unsettled()]
            return {"ok": True, **{k: v for k, v in self.runner.limits.snapshot().items()}, "allTasksSettled": not unsettled, "unsettled": unsettled,
                    "pendingApprovals": [a.view() for a in Approvals(self.board).list("pending")], "pause": self.paused_view(), "missingDecisions": [o.view() for o in decisions.missing(self.board)], "workers": ws, "stale": [s.model_dump(by_alias=True) for s in self.board.stale(10)],
                    "monitor": [{"worker": f.worker, "state": f.state, "detail": f.detail} for f in self.monitor.check()],
                    "digest": self.board.digest().text}
        except NoActiveSwarm as e:
            return self._err(e)

    # ---- shared tools -----------------------------------------------------------
    def board_post(self, agent: str, kind: str, body: str, task_id: str | None = None) -> dict:
        try:
            e = self.board.post(EventIn(agent=agent, kind=kind, body=body, task_id=task_id))  # type: ignore[arg-type]
        except (ValueError, NoActiveSwarm) as ex:
            return self._err(ex)
        return {"ok": True, "seq": e.seq, "id": e.id}

    def board_digest(self, since_seq: int = 0) -> dict:
        try:
            return {"ok": True, **self.board.digest(since_seq).model_dump(by_alias=True)}
        except NoActiveSwarm as e:
            return self._err(e)


INSTRUCTIONS = (
    "Swarm: coordinate named worker agents. Flow: swarm_start -> define_worker (disjoint owned paths) -> "
    "invoke_worker (non-blocking) -> wait_for_workers (bounded wait; call again if timedOut) -> review newEvents. "
    "Explain your choices with record_decision (split, escalations, open critic blockers): merge_workers refuses until you do. "
    f"Board kinds: {', '.join(EVENT_KINDS)}."
)


def build_server(tools: SwarmTools, role: str = "coordinator") -> MCPServer:
    srv = MCPServer("swarm", instructions=INSTRUCTIONS)
    srv.add_tool(tools.board_post, name="board_post", description="Post an event to the shared swarm board.")
    srv.add_tool(tools.board_digest, name="board_digest", description="Compact summary of the board (cheaper than reading history).")
    if role == "coordinator":
        srv.add_tool(tools.swarm_start, name="swarm_start", description="Start a new swarm for a task (archives the previous one).")
        srv.add_tool(tools.define_worker, name="define_worker", description=(
            "Register a named worker: goal, owns (paths it may write, relative to its own git worktree; must not overlap "
            "other workers), optional group (message every member at once with @<group>), optional extra allowed tools like 'Bash(pytest:*)', model (sonnet|haiku|opus), and checks: the exact "
            "commands that prove the goal (e.g. 'python3 -m pytest tests/test_api.py'). The verifier must run every check before it "
            "may pass the task, and the integration verifier runs all of them on the merged branch; each must be allowed by tools."))
        srv.add_tool(tools.critique_plan, name="critique_plan", description=(
            "Run a read-only plan critic on your plan before invoking workers (wait_for_workers, then read `critique` events and fix the plan). "
            "Pass critics=true to swarm_start to also have diff critics review each worker before the verifier: one combined critic for "
            "small diffs, three (correctness/security/tests) for big ones, none for docs-only diffs; blockers gate, majors go back once."))
        srv.add_tool(tools.define_researcher, name="define_researcher", description=(
            "Register a read-only research agent (repo + web, cites sources). `question` is one focused question. Wake it with "
            "invoke_worker; it posts `research` events. Max 4 per swarm. Use before planning or when a worker is blocked on facts."))
        srv.add_tool(tools.invoke_worker, name="invoke_worker", description=(
            "@mention a worker with a message. Non-blocking; wakes the worker's persistent session (no tokens used while idle). "
            "Mentions sent while it is busy are queued and merged. `name` may also be a group (every member) or `all` (every worker)."))
        srv.add_tool(tools.set_worker_model, name="set_worker_model", description=(
            "Move an idle worker to another model (sonnet|haiku|opus); it keeps its conversation. `why` is required and recorded "
            "as a decision. The first turn on the new model re-reads the whole conversation without the prompt cache, so it costs "
            "more than a normal turn. Ask the user before moving to a more expensive model."))
        srv.add_tool(tools.wait_for_workers, name="wait_for_workers", description=(
            "Block (max ~110s) until the workers are idle; returns statuses, last results and new board events. "
            "Check `timedOut` and call again to keep waiting."))
        srv.add_tool(tools.merge_workers, name="merge_workers", description=(
            "Merge all verified (done) worker branches into swarm/<id>/integration in its own worktree. Conflicts go to an "
            "integrator worker; wait_for_workers then call again. Once allMerged, call with verify=true: a read-only verifier runs "
            "the granted test commands on the merged branch (wait_for_workers, then check swarm_status task `integrator`)."))
        srv.add_tool(tools.record_decision, name="record_decision", description=(
            "Record why you made a choice; it goes on the board and into the run report. topic: split (why this split and these owned "
            "paths), escalation (worker=<name>: what you did and told the user), critic (worker=<name> or `plan`: how each blocker was "
            "fixed or why it was overridden), other. merge_workers and finishing are refused while a required one is missing "
            "(see `missingDecisions` in swarm_status)."))
        srv.add_tool(tools.swarm_status, name="swarm_status", description="Workers, stale workers, decisions you still owe and the board digest.")
    return srv


def main() -> int:
    root = find_root()
    ensure_swarm_dir(root)
    board = open_board(root)
    runner = Runner(board, root)
    tools = SwarmTools(board, runner, Gate(runner, verifier_model=os.environ.get("SWARM_VERIFIER_MODEL", "sonnet"),
                                           critic_model=os.environ.get("SWARM_CRITIC_MODEL") or None))
    if os.environ.get("SWARM_MONITOR_LLM") == "1":
        from .monitor import claude_judge
        tools.monitor.judge = claude_judge(runner.claude_bin)
    if os.environ.get("SWARM_MONITOR", "1") != "0":
        tools.monitor.start(float(os.environ.get("SWARM_MONITOR_INTERVAL", "15")))
    build_server(tools, os.environ.get("SWARM_ROLE", "coordinator")).run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
