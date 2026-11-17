"""Worker registry + cross-process mention queue (state lives in the board's SQLite db)."""
from __future__ import annotations

import json
import os
import uuid

from .board import Board
from .models import Worker, WorkerSpec, new_id, utc_now
from .limits import BudgetExceeded, LimitsStore  # noqa: F401
from .tasks import Tasks


class WorkerError(Exception):
    pass


class WorkerBudgetError(WorkerError):
    """Defining a worker would exceed the swarm's worker limit."""


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _worker(r) -> Worker:
    d = dict(r)
    d["owns"], d["tools"] = json.loads(d["owns"]), json.loads(d["tools"])
    d["recent_tools"] = json.loads(d.get("recent_tools") or "[]")
    return Worker(**d)


class Registry:
    def __init__(self, board: Board):
        self.b = board

    def define(self, spec: WorkerSpec, *, exempt_limit: bool = False) -> Worker:
        sw = self.b._require()
        try:
            LimitsStore(self.b).check_define(exempt=exempt_limit)
        except BudgetExceeded as e:
            raise WorkerBudgetError(str(e)) from e
        now = utc_now()
        w = Worker(id=new_id("wrk"), swarm_id=sw.id, name=spec.name, goal=spec.goal, owns=spec.owns,
                   tools=spec.tools, model=spec.model, session_id=str(uuid.uuid4()), status="idle",
                   created_at=now, updated_at=now)
        with self.b._conn() as c:
            if c.execute("SELECT 1 FROM workers WHERE swarm_id=? AND name=?", (sw.id, w.name)).fetchone():
                raise WorkerError(f"worker '{w.name}' already exists in this swarm")
            c.execute(
                "INSERT INTO workers (id,swarm_id,name,goal,owns,tools,model,session_id,status,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (w.id, w.swarm_id, w.name, w.goal, json.dumps(w.owns), json.dumps(w.tools), w.model,
                 w.session_id, w.status, now, now))
        Tasks(self.b).ensure(w.name, w.goal)
        return w

    def define_verifier(self, target: Worker, model: str = "sonnet") -> Worker:
        """Read-only verifier bound to the target worker's worktree; its own persistent session."""
        name, now = f"verify-{target.name}", utc_now()
        try:
            return self.get(name)
        except WorkerError:
            pass
        w = Worker(id=new_id("wrk"), swarm_id=target.swarm_id, name=name, goal=f"verify the work of {target.name}",
                   owns=[], tools=target.tools, model=model, session_id=str(uuid.uuid4()), status="idle",
                   role="verifier", cwd=target.worktree, created_at=now, updated_at=now)
        with self.b._conn() as c:
            c.execute("INSERT INTO workers (id,swarm_id,name,goal,owns,tools,model,session_id,status,role,cwd,created_at,updated_at)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (w.id, w.swarm_id, w.name, w.goal, "[]", json.dumps(w.tools), w.model, w.session_id, "idle",
                       "verifier", w.cwd, now, now))
        return w

    def set_cwd(self, w: Worker, cwd: str | None) -> None:
        with self.b._conn() as c:
            c.execute("UPDATE workers SET cwd=? WHERE id=?", (cwd, w.id))

    def set_base(self, w: Worker, sha: str) -> None:
        with self.b._conn() as c:
            c.execute("UPDATE workers SET base_sha=? WHERE id=?", (sha, w.id))

    def get(self, name: str) -> Worker:
        sw = self.b._require()
        with self.b._conn() as c:
            r = c.execute("SELECT * FROM workers WHERE swarm_id=? AND name=?", (sw.id, name)).fetchone()
        if r is None:
            raise WorkerError(f"no worker named '{name}' in the active swarm")
        return _worker(r)

    def list(self, include_verifiers: bool = False) -> list[Worker]:
        sw = self.b._require()
        with self.b._conn() as c:
            rows = c.execute("SELECT * FROM workers WHERE swarm_id=? ORDER BY created_at, name", (sw.id,)).fetchall()
        return [w for w in map(_worker, rows) if include_verifiers or w.role == "worker"]

    def set_worktree(self, w: Worker, path: str, branch: str) -> None:
        with self.b._conn() as c:
            c.execute("UPDATE workers SET worktree=?, branch=?, updated_at=? WHERE id=?", (path, branch, utc_now(), w.id))

    # ---- queue ------------------------------------------------------------
    def enqueue(self, w: Worker, sender: str, message: str) -> None:
        with self.b._conn() as c:
            c.execute("INSERT INTO worker_queue (worker_id,sender,message,ts) VALUES (?,?,?,?)",
                      (w.id, sender, message, utc_now()))

    def drain(self, w: Worker) -> list[tuple[str, str]]:
        """Atomically take all queued messages (FIFO)."""
        with self.b._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            rows = c.execute("SELECT id,sender,message FROM worker_queue WHERE worker_id=? ORDER BY id", (w.id,)).fetchall()
            if rows:
                c.execute("DELETE FROM worker_queue WHERE worker_id=? AND id<=?", (w.id, rows[-1]["id"]))
        return [(r["sender"], r["message"]) for r in rows]

    def queue_len(self, w: Worker) -> int:
        with self.b._conn() as c:
            return c.execute("SELECT COUNT(*) FROM worker_queue WHERE worker_id=?", (w.id,)).fetchone()[0]

    # ---- single-runner claim (one turn at a time per worker, across processes) ----
    def try_claim(self, w: Worker, pid: int | None = None) -> bool:
        pid = pid or os.getpid()
        with self.b._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT status,pid FROM workers WHERE id=?", (w.id,)).fetchone()
            if r["status"] == "running" and pid_alive(r["pid"]):  # also blocks other threads of this process
                return False
            c.execute("UPDATE workers SET status='running', pid=?, updated_at=? WHERE id=?", (pid, utc_now(), w.id))
        return True

    def finish_turn(self, w: Worker, *, ok: bool, cost: float = 0.0, error: str | None = None) -> None:
        with self.b._conn() as c:
            c.execute("UPDATE workers SET turns=turns+?, cost_usd=cost_usd+?, last_error=?, updated_at=? WHERE id=?",
                      (1 if ok else 0, cost, None if ok else error, utc_now(), w.id))

    def release(self, w: Worker, *, error: bool = False) -> None:
        with self.b._conn() as c:
            c.execute("UPDATE workers SET status=?, pid=NULL, updated_at=? WHERE id=?",
                      ("error" if error else "idle", utc_now(), w.id))

    # ---- activity (written by the streaming executor, read by the monitor) -------
    def touch(self, name: str, note: str | None = None, sig: str | None = None, reset: bool = False) -> None:
        sw = self.b._require()
        with self.b._conn() as c:
            if reset:
                c.execute("UPDATE workers SET last_activity=?, activity_note=?, tool_sig=NULL, tool_repeat=0 WHERE swarm_id=? AND name=?",
                          (utc_now(), note, sw.id, name))
            elif sig is not None:
                c.execute("UPDATE workers SET last_activity=?, activity_note=?, "
                          "tool_repeat=CASE WHEN tool_sig=? THEN tool_repeat+1 ELSE 1 END, tool_sig=? WHERE swarm_id=? AND name=?",
                          (utc_now(), note, sig, sig, sw.id, name))
            else:
                c.execute("UPDATE workers SET last_activity=?, activity_note=COALESCE(?,activity_note) WHERE swarm_id=? AND name=?",
                          (utc_now(), note, sw.id, name))

    def record_tool(self, name: str, label: str, keep: int = 8) -> None:
        sw = self.b._require()
        with self.b._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT recent_tools FROM workers WHERE swarm_id=? AND name=?", (sw.id, name)).fetchone()
            if r is None:
                return
            tools = (json.loads(r["recent_tools"] or "[]") + [label[:140]])[-keep:]
            c.execute("UPDATE workers SET recent_tools=? WHERE swarm_id=? AND name=?", (json.dumps(tools), sw.id, name))

    def bump_gap(self, name: str, gap: float) -> None:
        sw = self.b._require()
        with self.b._conn() as c:
            c.execute("UPDATE workers SET max_gap_s=MAX(max_gap_s, ?) WHERE swarm_id=? AND name=?", (round(gap, 2), sw.id, name))

    def set_child_pid(self, name: str, pid: int | None) -> None:
        sw = self.b._require()
        with self.b._conn() as c:
            c.execute("UPDATE workers SET child_pid=? WHERE swarm_id=? AND name=?", (pid, sw.id, name))

    # ---- monitor ladder state ------------------------------------------------------
    def mon_get(self, name: str) -> dict:
        sw = self.b._require()
        with self.b._conn() as c:
            r = c.execute("SELECT nudges,restarts,last_action,judged FROM monitor_state WHERE swarm_id=? AND worker=?", (sw.id, name)).fetchone()
        return dict(r) if r else {"nudges": 0, "restarts": 0, "last_action": None, "judged": 0}

    def mon_set(self, name: str, *, nudges: int, restarts: int, last_action: str | None, judged: int | None = None) -> None:
        sw = self.b._require()
        if judged is None:
            judged = self.mon_get(name)["judged"] if last_action is not None else 0
        with self.b._conn() as c:
            c.execute("INSERT INTO monitor_state (swarm_id,worker,nudges,restarts,last_action,judged) VALUES (?,?,?,?,?,?) "
                      "ON CONFLICT(swarm_id,worker) DO UPDATE SET nudges=excluded.nudges, restarts=excluded.restarts, "
                      "last_action=excluded.last_action, judged=excluded.judged",
                      (sw.id, name, nudges, restarts, last_action, judged))
