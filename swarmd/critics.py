"""Critic findings. Critics attack work (a plan before workers start, a worker's diff before the verifier); the verifier
checks the goal afterwards. Findings are severity-tagged; only `blocker`s gate a task. Rounds are capped (MAX_CRITIC_ROUNDS)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .board import Board
from .models import EventIn, utc_now

MAX_CRITIC_ROUNDS = 2
SEVERITIES = ("blocker", "major", "nit")
PERSONAS = {
    "correctness": "logic errors, unmet requirements, unhandled edge cases and error paths, off-by-one, wrong assumptions about other modules",
    "security": "injection, unsafe file/shell/network use, secrets, authz gaps, unvalidated input, unsafe deserialization, dependency risk",
    "tests": "missing or weak tests for the goal, tests that cannot fail, untested error paths, unmaintainable structure",
}
LOCATION = re.compile(r".+:\d+")


class CritiqueError(Exception):
    pass


@dataclass
class Finding:
    id: int
    target: str
    critic: str
    round: int
    severity: str
    location: str
    reason: str


class Critiques:
    def __init__(self, board: Board):
        self.b = board

    def rounds(self, target: str) -> int:
        sw = self.b._require()
        with self.b._conn() as c:
            r = c.execute("SELECT n FROM critic_rounds WHERE swarm_id=? AND target=?", (sw.id, target)).fetchone()
        return r["n"] if r else 0

    def begin_round(self, target: str) -> int:
        sw = self.b._require()
        with self.b._conn() as c:
            c.execute("INSERT INTO critic_rounds (swarm_id,target,n) VALUES (?,?,1) "
                      "ON CONFLICT(swarm_id,target) DO UPDATE SET n=n+1", (sw.id, target))
        return self.rounds(target)

    def add(self, target: str, critic: str, severity: str, reason: str, location: str = "") -> Finding:
        if severity not in SEVERITIES:
            raise CritiqueError(f"severity must be one of {', '.join(SEVERITIES)}")
        reason = reason.strip()
        if not reason:
            raise CritiqueError("a finding needs a concrete reason (what fails, with what input)")
        if severity != "nit" and target != "plan" and not LOCATION.fullmatch(location.strip()):
            raise CritiqueError("blocker/major findings must name a location as file:line (--at path/to/file.py:42)")
        rnd, sw = max(1, self.rounds(target)), self.b._require()
        with self.b._conn() as c:
            cur = c.execute("INSERT INTO critiques (swarm_id,target,critic,round,severity,location,reason,ts) VALUES (?,?,?,?,?,?,?,?)",
                            (sw.id, target, critic, rnd, severity, location.strip(), reason, utc_now()))
            fid = cur.lastrowid
        where = f" @ {location.strip()}" if location.strip() else ""
        self.b.post(EventIn(agent=critic, kind="critique", body=f"{severity} {target}{where}: {reason}"[:7900]))
        return Finding(fid, target, critic, rnd, severity, location.strip(), reason)

    def list(self, target: str, rnd: int | None = None, severity: str | None = None) -> list[Finding]:
        sw = self.b._require()
        q, args = "SELECT * FROM critiques WHERE swarm_id=? AND target=?", [sw.id, target]
        if rnd is not None:
            q += " AND round=?"; args.append(rnd)
        if severity:
            q += " AND severity=?"; args.append(severity)
        with self.b._conn() as c:
            rows = c.execute(q + " ORDER BY id", args).fetchall()
        return [Finding(r["id"], r["target"], r["critic"], r["round"], r["severity"], r["location"], r["reason"]) for r in rows]

    def blockers(self, target: str, rnd: int | None = None) -> list[Finding]:
        return self.list(target, rnd, "blocker")


def fmt(fs: list[Finding]) -> str:
    return "\n".join(f"{i}. [{f.severity}] {f.location or '-'}: {f.reason} (by {f.critic})" for i, f in enumerate(fs, 1))
