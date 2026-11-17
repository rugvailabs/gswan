#!/usr/bin/env python3
"""Stand-in for `claude -p` in e2e: posts an update to the board as the worker, then returns a stream-json result."""
import json, os, subprocess, sys

args = sys.argv[1:]
prompt = args[1] if len(args) > 1 and args[0] == "-p" else ""
print(json.dumps({"type": "system", "subtype": "init"}), flush=True)
worker = os.environ.get("SWARM_WORKER", "?")
subprocess.run([sys.executable, "-m", "swarmd.cli", "post", worker, "update", f"got: {prompt[:80]}"], check=False)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok",
                  "total_cost_usd": 0.0123, "permission_denials": []}), flush=True)
