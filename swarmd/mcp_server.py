"""MCP server (stdio) exposing the swarm to Claude Code agents.

Coordinator (default): swarm_start, define_worker, invoke_worker, wait_for_workers, swarm_status, board_post, board_digest.
Workers (SWARM_ROLE=worker): board_post, board_digest only.
`invoke_worker` is non-blocking (a turn can take minutes); use `wait_for_workers` to block with a bounded timeout.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

from mcp.server.mcpserver import MCPServer

from .board import Board, NoActiveSwarm
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

    def _busy(self, name: str) -> bool:
        t = self.threads.get(name)
        if t and t.is_alive():
            return True
        for n in (name, f"verify-{name}"):
            try:
                w = self.runner.reg.get(n)
            except (WorkerError, NoActiveSwarm):
                continue
            if w.status == "running" or self.runner.reg.queue_len(w) > 0:
                return True
        return False

    def _worker_view(self, w) -> dict:
        try:
            t = self.tasks.get(w.name)
            task = {"id": t.id, "status": t.status, "failedRounds": t.rounds, "reasons": t.reasons}
        except Exception:
            task = None
        return {"task": task, "name": w.name, "status": w.status, "busy": self._busy(w.name), "turns": w.turns,
                "costUsd": round(w.cost_usd, 4), "owns": w.owns, "branch": w.branch, "model": w.model, "queue": self.runner.reg.queue_len(w),
                "recentTools": w.recent_tools, "lastActivity": w.last_activity, "activityNote": w.activity_note,
                "worktree": w.worktree, "lastError": w.last_error, "lastResult": self.last.get(w.name)}

    # ---- coordinator tools ----------------------------------------------------
    def swarm_start(self, task: str, max_workers: int | None = None, max_wakeups: int | None = None,
                    max_wall_s: int | None = None, max_cost_usd: float | None = None) -> dict:
        """Start a swarm. Budgets default to 6 workers / 40 wake-ups / 3600s / $10; only a human can raise them later."""
        sw = self.board.start_swarm(task, limits={"max_workers": max_workers, "max_wakeups": max_wakeups,
                                                   "max_wall_s": max_wall_s, "max_cost_usd": max_cost_usd})
        self.threads.clear(); self.last.clear(); self.cursor = 0
        return {"ok": True, "swarmId": sw.id, "task": sw.task, **self.runner.limits.snapshot()}

    def define_worker(self, name: str, goal: str, owns: list[str], tools: list[str] | None = None,
                      model: str = "sonnet") -> dict:
        try:
            w = self.runner.define(WorkerSpec(name=name, goal=goal, owns=owns, tools=tools or [], model=model))
        except (ValueError, WorkerError, NoActiveSwarm) as e:
            return self._err(e)
        return {"ok": True, "worker": w.name, "id": w.id, "owns": w.owns}

    def invoke_worker(self, name: str, message: str, sender: str = "coordinator") -> dict:
        """@mention a worker. Returns immediately; the turn runs in the background."""
        try:
            self.runner.reg.get(name)
        except (WorkerError, NoActiveSwarm) as e:
            return self._err(e)

        def run() -> None:
            try:
                res = self.gate.invoke(name, message, sender=sender)
                self.last[name] = {"ok": res.ok, "text": res.text[:SUMMARY_CHARS], "costUsd": res.cost_usd,
                                   "denials": res.denials, "turns": res.turns_run, "queuedOnly": res.queued_only}
            except (RunnerError, WorkerError, NoActiveSwarm, ValueError) as e:
                self.last[name] = {"ok": False, "text": str(e)}

        with self.lock:
            t = threading.Thread(target=run, name=f"worker-{name}", daemon=True)
            self.threads[name] = t
            t.start()
        return {"ok": True, "started": name}

    def wait_for_workers(self, names: list[str] | None = None, timeout_s: float = 90) -> dict:
        """Block until the named workers (default: all) are idle with empty queues, or timeout."""
        timeout = max(1.0, min(float(timeout_s), MAX_WAIT))
        try:
            targets = names or [w.name for w in self.runner.reg.list()]
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
        return {"ok": True, "timedOut": any(self._busy(n) for n in targets), "workers": views,
                "newEvents": [f"#{e.seq} {e.agent}/{e.kind}: {e.body[:300]}" for e in new]}  # events since the previous wait

    def merge_workers(self, auto_resolve: bool = True, order: list[str] | None = None, verify: bool = False) -> dict:
        """Merge every worker whose task is `done` into the integration branch (never your checked-out branch).
        On a conflict an `integrator` worker resolves it (wait_for_workers, then call this again to continue)."""
        try:
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

    def set_limits(self, **kw) -> dict:
        """Human-only (HTTP API / CLI), deliberately not an MCP tool: the coordinator cannot raise its own budget."""
        try:
            self.runner.limits.set(**kw)
        except (ValueError, NoActiveSwarm) as e:
            return self._err(e)
        return {"ok": True, **self.runner.limits.snapshot()}

    def swarm_status(self) -> dict:
        try:
            ws = [self._worker_view(w) for w in self.runner.reg.list()]
            unsettled = [t.worker for t in self.tasks.unsettled()]
            return {"ok": True, **{k: v for k, v in self.runner.limits.snapshot().items()}, "allTasksSettled": not unsettled, "unsettled": unsettled, "workers": ws, "stale": [s.model_dump(by_alias=True) for s in self.board.stale(10)],
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
            "other workers), optional extra allowed tools like 'Bash(pytest:*)', model (sonnet|haiku|opus)."))
        srv.add_tool(tools.invoke_worker, name="invoke_worker", description=(
            "@mention a worker with a message. Non-blocking; wakes the worker's persistent session (zero cost while idle). "
            "Mentions sent while it is busy are queued and merged."))
        srv.add_tool(tools.wait_for_workers, name="wait_for_workers", description=(
            "Block (max ~110s) until the workers are idle; returns statuses, last results and new board events. "
            "Check `timedOut` and call again to keep waiting."))
        srv.add_tool(tools.merge_workers, name="merge_workers", description=(
            "Merge all verified (done) worker branches into swarm/<id>/integration in its own worktree. Conflicts go to an "
            "integrator worker; wait_for_workers then call again. Once allMerged, call with verify=true: a read-only verifier runs "
            "the granted test commands on the merged branch (wait_for_workers, then check swarm_status task `integrator`)."))
        srv.add_tool(tools.swarm_status, name="swarm_status", description="Workers, stale workers and the board digest.")
    return srv


def main() -> int:
    root = find_root()
    ensure_swarm_dir(root)
    board = open_board(root)
    runner = Runner(board, root)
    tools = SwarmTools(board, runner, Gate(runner, verifier_model=os.environ.get("SWARM_VERIFIER_MODEL", "sonnet")))
    if os.environ.get("SWARM_MONITOR_LLM") == "1":
        from .monitor import claude_judge
        tools.monitor.judge = claude_judge(runner.claude_bin)
    if os.environ.get("SWARM_MONITOR", "1") != "0":
        tools.monitor.start(float(os.environ.get("SWARM_MONITOR_INTERVAL", "15")))
    build_server(tools, os.environ.get("SWARM_ROLE", "coordinator")).run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
