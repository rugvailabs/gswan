"""Run report: what was planned vs what happened, who said what, what it cost. Everything comes from the board db and git,
so a human can audit a swarm after the fact without reading transcripts."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .board import Board
from .critics import Critiques
from .limits import LimitsStore
from .tasks import Tasks
from .workers import Registry


def in_scope(path: str, owns: list[str]) -> bool:
    return any(path == o.rstrip("/") or path.startswith(o.rstrip("/") + "/") for o in owns)


def changed_files(root: Path, base: str | None, branch: str | None) -> list[str] | None:
    """Files the worker's branch changed since its base commit; None when git cannot tell (no branch yet)."""
    if not (base and branch):
        return None
    r = subprocess.run(["git", "-C", str(root), "diff", "--name-only", f"{base}..{branch}"], capture_output=True, text=True)
    return [x for x in r.stdout.splitlines() if x] if r.returncode == 0 else None


def build(board: Board, root: Path) -> dict:
    sw = board._require()
    reg, tasks, crit = Registry(board), Tasks(board), Critiques(board)
    events = board.list_events(limit=500).items
    by_kind = lambda k: [e for e in events if e.kind == k]  # noqa: E731
    workers = []
    for w in reg.list():
        t = tasks.get(w.name)
        files = changed_files(root, w.base_sha, w.branch)
        out = [f for f in (files or []) if not in_scope(f, w.owns)]
        fs = crit.list(w.name)
        workers.append({"name": w.name, "goal": w.goal, "owns": w.owns, "status": w.status, "model": w.model, "turns": w.turns,
                        "costUsd": round(w.cost_usd, 4), "branch": w.branch, "task": t.status, "failedRounds": t.rounds,
                        "reasons": t.reasons, "filesChanged": files, "outOfScope": out,
                        "critiques": {s: sum(1 for f in fs if f.severity == s) for s in ("blocker", "major", "nit")},
                        "criticRounds": crit.rounds(w.name)})
    with board._conn() as c:
        by_role = {r["role"]: round(r["cost"], 4) for r in c.execute(
            "SELECT role, COALESCE(SUM(cost_usd),0) AS cost FROM workers WHERE swarm_id=? GROUP BY role", (sw.id,))}
        n_roles = {r["role"]: r["n"] for r in c.execute("SELECT role, COUNT(*) AS n FROM workers WHERE swarm_id=? GROUP BY role", (sw.id,))}
    denied: list[str] = []
    log = root / ".swarm" / "denied.log"
    if log.is_file():
        denied = log.read_text().splitlines()[-20:]
    return {"swarmId": sw.id, "task": sw.task, "startedAt": sw.created_at, **LimitsStore(board).snapshot(),
            "costByRole": by_role, "agentsByRole": n_roles, "workers": workers,
            "plan": [e.body for e in by_kind("plan")], "decisions": [f"#{e.seq} {e.body}" for e in by_kind("decision")],
            "research": [f"#{e.seq} {e.agent}: {e.body}" for e in by_kind("research")],
            "critiques": [f"#{e.seq} {e.body}" for e in by_kind("critique")],
            "verdicts": [f"#{e.seq} {e.body}" for e in by_kind("verify")],
            "monitor": [f"#{e.seq} {e.body}" for e in by_kind("nudge")],
            "blocked": [f"#{e.seq} {e.agent}: {e.body}" for e in by_kind("blocked")],
            "deniedTail": denied, "events": len(events)}


def markdown(d: dict) -> str:
    L = [f"# Run report — {d['task']}", "", f"swarm `{d['swarmId']}` · started {d['startedAt']} · {d['events']} board events", ""]
    u, lim = d["usage"], d["limits"]
    L += ["## Budget", f"workers {u['workers']}/{lim['max_workers']} · wake-ups {u['wakeups']}/{lim['max_wakeups']} · "
          f"wall {u['wall_s']}s/{lim['max_wall_s']}s · cost ${u['cost_usd']}/${lim['max_cost_usd']}", "",
          "Cost by role: " + (", ".join(f"{r} ${c} ({d['agentsByRole'].get(r, 0)} agents)" for r, c in d["costByRole"].items()) or "none"), ""]
    L += ["## Plan vs actual", "", "| worker | goal | owns | files changed | out of scope | task | verifier fails | critic blk/maj/nit | cost |", "|---|---|---|---|---|---|---|---|---|"]
    for w in d["workers"]:
        fc = "?" if w["filesChanged"] is None else str(len(w["filesChanged"]))
        oos = ", ".join(w["outOfScope"]) or "-"
        c = w["critiques"]
        L.append(f"| {w['name']} | {w['goal'][:60]} | {', '.join(w['owns'])} | {fc} | {oos} | {w['task']} | {w['failedRounds']} | "
                 f"{c['blocker']}/{c['major']}/{c['nit']} | ${w['costUsd']} |")
    if any(w["outOfScope"] for w in d["workers"]):
        L += ["", "**Scope drift:** at least one worker changed files outside its owned paths (see table)."]
    for title, key in (("Decisions", "decisions"), ("Research findings", "research"), ("Critic findings", "critiques"),
                       ("Verifier verdicts", "verdicts"), ("Monitor actions (signal and rule that triggered each)", "monitor"), ("Blocked", "blocked")):
        L += ["", f"## {title}"] + ([f"- {x}" for x in d[key]] or ["- none"])
    L += ["", "## Denied tool calls (last 20)"] + ([f"- {x}" for x in d["deniedTail"]] or ["- none"])
    return "\n".join(L) + "\n"


def as_json(d: dict) -> str:
    return json.dumps(d, indent=2)
