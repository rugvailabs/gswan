"""Critic findings. Critics attack work (a plan before workers start, a worker's diff before the verifier); the verifier
checks the goal afterwards. Findings are severity-tagged; `blocker`s gate a task, `major`s go back to the worker once (fix or
explain) without another critic round. Rounds are capped (MAX_CRITIC_ROUNDS).

Cost control (a real run spent 58% of its tokens on critics that found no blockers): a diff that only touches docs gets no
critics, and a small diff gets one combined critic instead of one per persona (`choose_personas`)."""
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
PERSONAS["general"] = "; ".join(f"{k}: {v}" for k, v in PERSONAS.items())  # one critic covering all of them, for small diffs
SMALL_DIFF = 200  # changed lines (added + deleted) below which one combined critic reviews instead of one per persona
DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc")


def is_doc(path: str) -> bool:
    return path.lower().endswith(DOC_SUFFIXES) or path.startswith("docs/")


def choose_personas(personas: tuple[str, ...], changes: list[tuple[str, int]] | None) -> tuple[tuple[str, ...], str]:
    """Which critics review a diff. `changes` = [(path, changed lines)] from git, or None when git cannot tell.
    Returns (personas, why); empty personas = skip critics."""
    if not changes:
        return personas, ""  # unknown or empty diff: keep the configured critics
    if all(is_doc(p) for p, _ in changes):
        return (), "only documentation changed"
    lines = sum(n for _, n in changes)
    if lines < SMALL_DIFF and len(personas) > 1:
        return ("general",), f"small diff ({lines} changed lines): one combined critic"
    return personas, ""
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
    ts: str = ""


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
        rnd, sw, ts = max(1, self.rounds(target)), self.b._require(), utc_now()
        with self.b._conn() as c:
            cur = c.execute("INSERT INTO critiques (swarm_id,target,critic,round,severity,location,reason,ts) VALUES (?,?,?,?,?,?,?,?)",
                            (sw.id, target, critic, rnd, severity, location.strip(), reason, ts))
            fid = cur.lastrowid
        where = f" @ {location.strip()}" if location.strip() else ""
        self.b.post(EventIn(agent=critic, kind="critique", body=f"{severity} {target}{where}: {reason}"[:7900]))
        return Finding(fid, target, critic, rnd, severity, location.strip(), reason, ts)

    def list(self, target: str, rnd: int | None = None, severity: str | None = None) -> list[Finding]:
        sw = self.b._require()
        q, args = "SELECT * FROM critiques WHERE swarm_id=? AND target=?", [sw.id, target]
        if rnd is not None:
            q += " AND round=?"; args.append(rnd)
        if severity:
            q += " AND severity=?"; args.append(severity)
        with self.b._conn() as c:
            rows = c.execute(q + " ORDER BY id", args).fetchall()
        return [Finding(r["id"], r["target"], r["critic"], r["round"], r["severity"], r["location"], r["reason"], r["ts"]) for r in rows]

    def blockers(self, target: str, rnd: int | None = None) -> list[Finding]:
        return self.list(target, rnd, "blocker")

    def majors(self, target: str, rnd: int | None = None) -> list[Finding]:
        return self.list(target, rnd, "major")

    def close(self, target: str) -> None:
        """No more critic rounds for this target (its majors went back to the worker; the verifier checks the rest)."""
        sw = self.b._require()
        with self.b._conn() as c:
            c.execute("INSERT INTO critic_rounds (swarm_id,target,n) VALUES (?,?,?) ON CONFLICT(swarm_id,target) DO UPDATE SET n=MAX(n,?)",
                      (sw.id, target, MAX_CRITIC_ROUNDS, MAX_CRITIC_ROUNDS))


def fmt(fs: list[Finding]) -> str:
    return "\n".join(f"{i}. [{f.severity}] {f.location or '-'}: {f.reason} (by {f.critic})" for i, f in enumerate(fs, 1))
