"""Stop hook for the COORDINATOR session: don't let it finish while worker tasks are unverified.

Blocks up to MAX_BLOCKS times per swarm (so a stuck verifier cannot trap the session forever);
escalated tasks (need a human) do not block. Workers/verifiers (SWARM_WORKER set) are never blocked.
"""
from __future__ import annotations

import json
import os
import sys

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
    except NoActiveSwarm:
        return None
    if not pending:
        return None
    counter = root / ".swarm" / f"stop-{sw.id}.count"
    n = int(counter.read_text()) if counter.exists() else 0
    if n >= MAX_BLOCKS:
        return None
    counter.write_text(str(n + 1))
    lines = ", ".join(f"{t.worker} [{t.status}]" for t in pending)
    return {"decision": "block", "reason": (
        f"Swarm {sw.id} still has unverified tasks: {lines}. Call wait_for_workers / swarm_status, handle blockers, and only "
        f"finish when every task is `done` (or `escalated` and reported to the user). ({n + 1}/{MAX_BLOCKS})")}


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
