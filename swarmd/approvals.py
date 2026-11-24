"""Human approval of denied commands: a denial becomes a question instead of a dead end.

When the Bash policy denies a worker's command, the guard files an approval request (deduplicated, at most
MAX_PENDING open per worker) and tells the worker not to wait. A human answers from the dashboard or the CLI:
  allow    this exact command is allowed for that worker from now on (whitespace-normalized, whole command)
  program  `Bash(<program>:*)` is added to the worker's grants (only when the denial was a program not granted)
  deny     the worker is told no, with the human's note
Then the worker is woken with the answer. Nothing is held open while waiting, so a pending request costs no tokens.
Agents can never decide: the CLI refuses inside a swarm session and there is no MCP tool for it.
"""
from __future__ import annotations

import json
import os
import shlex
from dataclasses import asdict, dataclass

from .board import Board
from .models import EventIn, new_id, utc_now

MAX_PENDING = 5  # open requests per worker; more are plain denials
DECISIONS = ("allow", "program", "deny")
REQUEST_ROLES = ("worker",)  # read-only roles stay read-only; their denials are only logged


class ApprovalError(Exception):
    pass


@dataclass
class Approval:
    id: str
    worker: str
    command: str
    reason: str
    program: str | None  # set when the denial was "program not granted", so it can be granted as a whole
    status: str  # pending | allowed | denied
    decision: str | None
    note: str
    created_at: str
    decided_at: str | None

    def view(self) -> dict:
        d = asdict(self)
        return {"id": d["id"], "worker": d["worker"], "command": d["command"], "reason": d["reason"], "program": d["program"],
                "status": d["status"], "decision": d["decision"], "note": d["note"], "createdAt": d["created_at"], "decidedAt": d["decided_at"]}


def norm(cmd: str) -> str:
    return " ".join(cmd.split())


def program_of(command: str, reason: str) -> str | None:
    """The program to grant, only when the policy's complaint was exactly that this program is not granted."""
    if "is not allowed. Granted commands" not in reason:
        return None
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    from .bashpolicy import _ASSIGN
    while words and _ASSIGN.match(words[0]):
        words.pop(0)
    prog = os.path.basename(words[0]) if words else ""
    return prog if prog and prog.replace("-", "").replace("_", "").replace(".", "").isalnum() else None


class Approvals:
    def __init__(self, board: Board):
        self.b = board

    def _row(self, r) -> Approval:
        return Approval(r["id"], r["worker"], r["command"], r["reason"], r["program"], r["status"], r["decision"], r["note"],
                        r["created_at"], r["decided_at"])

    def get(self, aid: str) -> Approval:
        sw = self.b._require()
        with self.b._conn() as c:
            r = c.execute("SELECT * FROM approvals WHERE swarm_id=? AND id=?", (sw.id, aid)).fetchone()
        if not r:
            raise ApprovalError(f"no approval request {aid}")
        return self._row(r)

    def list(self, status: str | None = None, worker: str | None = None) -> list[Approval]:
        sw = self.b._require()
        q, args = "SELECT * FROM approvals WHERE swarm_id=?", [sw.id]
        if status:
            q += " AND status=?"; args.append(status)
        if worker:
            q += " AND worker=?"; args.append(worker)
        with self.b._conn() as c:
            return [self._row(r) for r in c.execute(q + " ORDER BY created_at", args)]

    def allowed_commands(self, worker: str) -> set[str]:
        return {a.command for a in self.list("allowed", worker) if a.decision == "allow"}

    def request(self, worker: str, command: str, reason: str) -> tuple[Approval | None, str]:
        """File (or find) a request. Returns (request or None, a sentence for the worker)."""
        cmd = norm(command)
        if not cmd:
            return None, ""
        same = [a for a in self.list(worker=worker) if a.command == cmd]
        if any(a.status == "denied" for a in same):
            note = next(a.note for a in same if a.status == "denied")
            return None, f"A human already denied this exact command{': ' + note if note else ''}. Do not ask again; find another way or post `blocked`."
        if pending := next((a for a in same if a.status == "pending"), None):
            return pending, f"Approval request {pending.id} for it is already open. Do not wait or retry; continue with other work or post `blocked`."
        if len(self.list("pending", worker)) >= MAX_PENDING:
            return None, f"You already have {MAX_PENDING} open approval requests; post `blocked` for the coordinator instead."
        sw, now = self.b._require(), utc_now()
        a = Approval(new_id("apr"), worker, cmd, reason[:500], program_of(cmd, reason), "pending", None, "", now, None)
        with self.b._conn() as c:
            c.execute("INSERT INTO approvals (id,swarm_id,worker,command,reason,program,status,decision,note,created_at,decided_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (a.id, sw.id, a.worker, a.command, a.reason, a.program, a.status, None, "", now, None))
        self.b.post(EventIn(agent="swarmd", kind="approval", body=f"{a.id} {worker} asks to run: {cmd[:300]} (denied: {reason[:200]})"[:2000],
                            refs=[f"approval:{a.id}:pending", f"worker:{worker}"]))
        return a, (f"A human can approve it: request {a.id} is on the dashboard. Do NOT wait or retry it; continue with other work "
                   f"(or post `blocked` if you cannot). You will get a message with the answer.")

    def decide(self, aid: str, decision: str, note: str = "", by: str = "human") -> Approval:
        if decision not in DECISIONS:
            raise ApprovalError(f"decision must be one of {', '.join(DECISIONS)}")
        a = self.get(aid)
        if a.status != "pending":
            raise ApprovalError(f"request {aid} is already {a.status}")
        if decision == "program" and not a.program:
            raise ApprovalError("this denial was not about a missing program grant; allow the exact command instead")
        status, sw, now = ("denied" if decision == "deny" else "allowed"), self.b._require(), utc_now()
        with self.b._conn() as c:
            n = c.execute("UPDATE approvals SET status=?, decision=?, note=?, decided_at=? WHERE swarm_id=? AND id=? AND status='pending'",
                          (status, decision, note.strip()[:500], now, sw.id, aid)).rowcount
            if not n:
                raise ApprovalError(f"request {aid} was decided meanwhile")
            if decision == "program":
                grant = f"Bash({a.program}:*)"
                for name in (a.worker, f"verify-{a.worker}"):  # its verifier must be able to run the same checks
                    r = c.execute("SELECT tools FROM workers WHERE swarm_id=? AND name=?", (sw.id, name)).fetchone()
                    if r is not None:
                        tools = json.loads(r["tools"] or "[]")
                        if grant not in tools:
                            c.execute("UPDATE workers SET tools=? WHERE swarm_id=? AND name=?", (json.dumps(tools + [grant]), sw.id, name))
        what = {"allow": "allowed this command", "program": f"granted `{a.program}` (all its commands)", "deny": "denied it"}[decision]
        self.b.post(EventIn(agent=by, kind="approval", body=f"{aid} {by} {what} for {a.worker}: {a.command[:300]}" + (f" — {note.strip()}" if note.strip() else ""),
                            refs=[f"approval:{aid}:{status}", f"worker:{a.worker}"]))
        return self.get(aid)

    @staticmethod
    def message(a: Approval) -> str:
        """What the worker is told once a human decided."""
        if a.decision == "allow":
            return f"A human approved your command (request {a.id}). Run it now, exactly as before, and continue:\n{a.command}"
        if a.decision == "program":
            return f"A human granted you `{a.program}` (request {a.id}): any `{a.program} ...` command is allowed now. Run it and continue:\n{a.command}"
        return (f"A human DENIED your command (request {a.id}): {a.command}" + (f"\nTheir note: {a.note}" if a.note else "")
                + "\nDo not retry it. Find another way within your allowed commands, or post `blocked` explaining what you need.")
