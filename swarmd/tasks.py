"""Tasks and verdicts. A worker's `done` is provisional; only a verifier verdict makes a task `done`."""
from __future__ import annotations

import json

from .board import Board
from .models import EventIn, Task, new_id, utc_now

MAX_ROUNDS = 2  # failed verifications before escalating to the human


class TaskError(Exception):
    pass


def _task(r) -> Task:
    d = dict(r); d["reasons"] = json.loads(d["reasons"])
    return Task(**d)


class Tasks:
    def __init__(self, board: Board):
        self.b = board

    def ensure(self, worker: str, goal: str) -> Task:
        sw, now = self.b._require(), utc_now()
        with self.b._conn() as c:
            c.execute("INSERT OR IGNORE INTO tasks (id,swarm_id,worker,goal,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                      (new_id("tsk"), sw.id, worker, goal, now, now))
        return self.get(worker)

    def get(self, worker: str) -> Task:
        sw = self.b._require()
        with self.b._conn() as c:
            r = c.execute("SELECT * FROM tasks WHERE swarm_id=? AND worker=?", (sw.id, worker)).fetchone()
        if r is None:
            raise TaskError(f"no task for worker '{worker}'")
        return _task(r)

    def list(self) -> list[Task]:
        sw = self.b._require()
        with self.b._conn() as c:
            return [_task(r) for r in c.execute("SELECT * FROM tasks WHERE swarm_id=? ORDER BY created_at, worker", (sw.id,))]

    def set_status(self, worker: str, status: str, *, only_from: tuple[str, ...] | None = None) -> bool:
        sw = self.b._require()
        q, args = "UPDATE tasks SET status=?, updated_at=? WHERE swarm_id=? AND worker=?", [status, utc_now(), sw.id, worker]
        if only_from:
            q += f" AND status IN ({','.join('?' * len(only_from))})"; args += list(only_from)
        with self.b._conn() as c:
            return c.execute(q, args).rowcount > 0

    def request_verification(self, worker: str) -> Task:
        t = self.get(worker)
        if t.status in ("open", "failed"):
            self.set_status(worker, "awaiting_verification")
        return self.get(worker)

    def submit_verdict(self, worker: str, passed: bool, reasons: list[str], verifier: str | None = None) -> Task:
        t = self.get(worker)
        if t.status not in ("verifying", "awaiting_verification"):
            raise TaskError(f"task for '{worker}' is '{t.status}', not under verification")
        who = verifier or f"verify-{worker}"
        reasons = [r.strip() for r in reasons if r.strip()]
        if not passed and not reasons:
            raise TaskError("a failing verdict needs at least one concrete reason")
        sw = self.b._require()
        if passed:
            with self.b._conn() as c:
                c.execute("UPDATE tasks SET status='done', reasons='[]', updated_at=? WHERE swarm_id=? AND worker=?", (utc_now(), sw.id, worker))
            self.b.post(EventIn(agent=who, kind="verify", body=f"pass {worker}: " + ("; ".join(reasons) or "verified"), refs=[f"task:{worker}:done"]))
        else:
            with self.b._conn() as c:
                c.execute("UPDATE tasks SET status='failed', rounds=rounds+1, reasons=?, updated_at=? WHERE swarm_id=? AND worker=?",
                          (json.dumps(reasons), utc_now(), sw.id, worker))
            self.b.post(EventIn(agent=who, kind="verify", body=f"fail {worker}: " + " | ".join(f"{i}. {r}" for i, r in enumerate(reasons, 1)), refs=[f"task:{worker}:failed"]))
        return self.get(worker)

    def missing_checks(self, worker: str, checks: list[str], verifier: str) -> list[str]:
        """Required checks the verifier has not run since this verification round started."""
        if not checks:
            return []
        t, sw = self.get(worker), self.b._require()
        with self.b._conn() as c:
            ran = [" ".join(r["command"].split()) for r in c.execute(
                "SELECT command FROM ran_commands WHERE swarm_id=? AND worker=? AND ts>=?", (sw.id, verifier, t.updated_at))]
        return [chk for chk in checks if not any(" ".join(chk.split()) in r for r in ran)]

    def unsettled(self) -> list[Task]:
        """Tasks that still block finishing the swarm (escalated ones need a human, so they do not block)."""
        return [t for t in self.list() if t.status not in ("done", "escalated")]


def record_run(board: Board, worker: str, command: str) -> None:
    sw = board._require()
    with board._conn() as c:
        c.execute("INSERT INTO ran_commands (swarm_id,worker,ts,command) VALUES (?,?,?,?)", (sw.id, worker, utc_now(), command[:2000]))
