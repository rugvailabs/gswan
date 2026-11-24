"""Per-worker diff for the dashboard: what the worker's branch changed since its base commit, file by file, next to
the critic findings and verifier verdicts about it. Read-only; everything comes from git and the board db."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .board import Board
from .critics import Critiques
from .redact import redact_secrets
from .report import in_scope
from .tasks import TaskError, Tasks
from .workers import Registry

MAX_FILES = 200           # files with a patch in one response
MAX_PATCH_CHARS = 60_000  # per file; the rest is cut and flagged


def _git(cwd: Path | str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, errors="replace")


def _files(root: Path, rng: str, owns: list[str]) -> tuple[list[dict] | None, int, str | None]:
    ns = _git(root, "diff", "--no-renames", "--name-status", "-z", rng)
    if ns.returncode != 0:
        return None, 0, (ns.stderr.strip() or "git diff failed")[:300]
    tok = [x for x in ns.stdout.split("\0") if x]
    status = dict(zip(tok[1::2], tok[0::2]))
    counts: dict[str, tuple[int | None, int | None]] = {}
    for line in _git(root, "diff", "--no-renames", "--numstat", "-z", rng).stdout.split("\0"):
        parts = line.split("\t", 2)
        if len(parts) == 3:
            counts[parts[2]] = (None if parts[0] == "-" else int(parts[0]), None if parts[1] == "-" else int(parts[1]))
    out = []
    for path in sorted(status)[:MAX_FILES]:
        adds, dels = counts.get(path, (0, 0))
        binary = adds is None
        raw = "" if binary else _git(root, "diff", "--no-renames", "--no-color", rng, "--", path).stdout
        at = raw.find("\n@@")
        patch = raw[at + 1:] if at >= 0 else ""  # drop the `diff --git`/index/+++ header; the UI shows the path itself
        safe = redact_secrets(patch)
        out.append({"path": path, "status": status[path], "additions": adds, "deletions": dels, "binary": binary,
                    "outOfScope": not in_scope(path, owns), "redacted": safe != patch,
                    "truncated": len(safe) > MAX_PATCH_CHARS, "patch": safe[:MAX_PATCH_CHARS]})
    return out, max(0, len(status) - MAX_FILES), None


def worker_diff(board: Board, root: Path, name: str) -> dict:
    """Raises WorkerError for an unknown worker."""
    w = Registry(board).get(name)
    try:
        t = Tasks(board).get(name)
        task = {"id": t.id, "status": t.status, "failedRounds": t.rounds, "reasons": t.reasons}
    except TaskError:
        task = None
    files, more, note = None, 0, None
    if w.base_sha and w.branch:
        files, more, note = _files(root, f"{w.base_sha}..{w.branch}", w.owns)
    else:
        note = "no branch yet: the worker has not run a turn"
    dirty: list[str] = []
    if w.worktree and Path(w.worktree).is_dir():
        dirty = [line[3:] for line in _git(w.worktree, "status", "--porcelain").stdout.splitlines() if line[3:]]
    verdicts = []
    for e in board.list_events(kind="verify", limit=500).items:
        for word, passed in (("pass", True), ("fail", False)):
            if e.body.startswith(f"{word} {name}:"):
                verdicts.append({"seq": e.seq, "passed": passed, "by": e.agent, "text": e.body[len(word) + len(name) + 2:].strip()})
    return {"worker": w.name, "role": w.role, "goal": w.goal, "owns": w.owns, "branch": w.branch, "base": w.base_sha, "task": task,
            "files": files, "moreFiles": more, "note": note, "uncommitted": dirty,
            "totals": {"files": len(files or []) + more, "additions": sum(f["additions"] or 0 for f in files or []),
                       "deletions": sum(f["deletions"] or 0 for f in files or [])},
            "critiques": [{"critic": f.critic, "round": f.round, "severity": f.severity, "location": f.location, "reason": f.reason}
                          for f in Critiques(board).list(name)],
            "verdicts": verdicts}
