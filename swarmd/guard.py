"""PreToolUse hook for swarm sessions: (1) deny file writes outside the worker's owned paths, (2) apply the Bash
command policy (bashpolicy.py): allow commands whose every part is permitted, deny the rest with a readable reason.

Env (set by the runner): SWARM_WORKER, SWARM_ROLE, SWARM_OWNS (os.pathsep-separated absolute dirs/files),
SWARM_WORKTREE, SWARM_ALLOW (JSON list of granted `Bash(...)` tools), SWARM_PYTHON, SWARM_BRANCH_PREFIX.
If SWARM_WORKER is unset this is not a swarm session and everything is allowed.
Limitation: a granted program such as python3 can still write anywhere; grant narrowly.
"""
from __future__ import annotations

import json
import os
import sys

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _verdict(decision: str, reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}}


def _log_denial(env: dict[str, str], command: str, reason: str) -> None:
    """Append to .swarm/denied.log so humans can see WHY commands were denied (tool_input alone is truncated)."""
    import json as _json
    import time
    home = env.get("SWARM_HOME")
    if not home:
        return
    try:
        with open(os.path.join(home, ".swarm", "denied.log"), "a") as f:
            f.write(_json.dumps({"ts": time.strftime("%H:%M:%S"), "worker": env.get("SWARM_WORKER"), "reason": reason, "command": command[:400]}) + "\n")
    except OSError:
        pass  # logging must never break the hook


def decide_bash(payload: dict, env: dict[str, str]) -> dict:
    import json as _json
    from . import bashpolicy
    cmd = (payload.get("tool_input") or {}).get("command", "")
    try:
        grants = _json.loads(env.get("SWARM_ALLOW") or "[]")
    except ValueError:
        grants = []
    wt = env.get("SWARM_WORKTREE") or os.getcwd()
    try:
        bashpolicy.check(cmd, role=env.get("SWARM_ROLE", "worker"), worktree=wt, cwd=payload.get("cwd") or wt, grants=grants,
                         cli_python=env.get("SWARM_PYTHON"), branch_prefix=env.get("SWARM_BRANCH_PREFIX"))
    except bashpolicy.Denied as e:
        _log_denial(env, cmd, str(e))
        return _verdict("deny", f"swarm policy: {e}")
    return _verdict("allow", "allowed by swarm bash policy")


def decide(payload: dict, env: dict[str, str]) -> dict | None:
    worker = env.get("SWARM_WORKER")
    if not worker:
        return None
    if payload.get("tool_name") == "Bash":
        return decide_bash(payload, env)
    if payload.get("tool_name") not in WRITE_TOOLS:
        return None
    ti = payload.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    owns = [o for o in env.get("SWARM_OWNS", "").split(os.pathsep) if o]
    target = os.path.realpath(path) if path else ""
    ok = bool(target) and any(target == (r := os.path.realpath(o)) or target.startswith(r.rstrip(os.sep) + os.sep) for o in owns)
    if ok:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": f"worker '{worker}' may only write inside its owned paths: {', '.join(owns) or '(none)'}. "
                                    f"Post a 'blocked' event and ask the coordinator instead of writing {path or '?'}."}}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    out = decide(payload, dict(os.environ))
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
