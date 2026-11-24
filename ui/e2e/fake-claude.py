#!/usr/bin/env python3
"""Stand-in for `claude -p` in e2e: posts an update to the board as the worker, then returns a stream-json result."""
import json, os, subprocess, sys

args = sys.argv[1:]
prompt = args[1] if len(args) > 1 and args[0] == "-p" else ""
print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
worker = os.environ.get("SWARM_WORKER", "?")
subprocess.run([sys.executable, "-m", "swarmd.cli", "post", worker, "update", f"got: {prompt[:80]}"], check=False)
if os.environ.get("SWARM_ROLE") == "worker" and subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True).returncode == 0:
    os.makedirs(f"src/{worker}", exist_ok=True)
    with open(f"src/{worker}/notes.txt", "a") as fh:
        fh.write(f"got: {prompt[:80]}\n")
    subprocess.run(["git", "add", "-A"], check=False, capture_output=True)
    subprocess.run(["git", "commit", "-qm", f"{worker}: notes"], check=False, capture_output=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok",
                  "total_cost_usd": 0.0123, "permission_denials": []}), flush=True)
