"""Stop hook for the COORDINATOR session: don't let it finish while worker tasks are unverified
or while it owes a `decision` (see decisions.py).

Blocks up to MAX_BLOCKS times per swarm (so a stuck verifier cannot trap the session forever);
escalated tasks (need a human) do not block. Workers/verifiers (SWARM_WORKER set) are never blocked.
"""
from __future__ import annotations

import json
import os
import sys

from . import decisions
from .board import NoActiveSwarm
from .cli import find_root, open_board
from .tasks import Tasks

MAX_BLOCKS = 3


def decide(root, env) -> dict | None:
    if env.get("SWARM_WORKER") or not (root / ".swarm" / "swarm.db").exists():
        return None
    board = open_board(root)
    try:
        sw = board.current_swarm()
        if sw is None:
            return None
        pending = Tasks(board).unsettled()
        owed = decisions.missing(board)
    except NoActiveSwarm:
        return None
    if not pending and not owed:
        return None
    counter = root / ".swarm" / f"stop-{sw.id}.count"
    n = int(counter.read_text()) if counter.exists() else 0
    if n >= MAX_BLOCKS:
        return None
    counter.write_text(str(n + 1))
    parts = []
    if pending:
        lines = ", ".join(f"{t.worker} [{t.status}]" for t in pending)
        parts.append(f"still has unverified tasks: {lines}. Call wait_for_workers / swarm_status, handle blockers, and only "
                     f"finish when every task is `done` (or `escalated` and reported to the user).")
    if owed:
        parts.append(f"has decisions you have not explained: {decisions.owed_text(owed)}. Call record_decision for each "
                     f"(the reason goes into the run report the user reads).")
    return {"decision": "block", "reason": f"Swarm {sw.id} " + " It also ".join(parts) + f" ({n + 1}/{MAX_BLOCKS})"}


def main() -> int:
    try:
        json.load(sys.stdin)
    except json.JSONDecodeError:
        pass
    out = decide(find_root(), dict(os.environ))
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
