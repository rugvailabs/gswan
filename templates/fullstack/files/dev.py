"""Start the backend and the frontend together (Ctrl-C stops both). `python3 dev.py install` installs both first.

Uses only the standard library and runs on Python 3.9+ (macOS and Linux).
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ["uv", "run", "uvicorn", "app.main:app", "--reload", "--port", "8000"]
FRONTEND = ["npm", "run", "dev"]


def install() -> int:
    for cmd, cwd in ((["uv", "sync"], "backend"), (["npm", "install"], "frontend")):
        print(f"[{cwd}] {' '.join(cmd)}", flush=True)
        if subprocess.call(cmd, cwd=ROOT / cwd) != 0:
            return 1
    return 0


def _stop(p: subprocess.Popen) -> None:
    """Stop a part and everything it started (npm and uv start child processes)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if p.poll() is not None:
            return
        try:
            os.killpg(p.pid, sig)
        except ProcessLookupError:
            return
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def run() -> int:
    def on_term(*_: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    procs = [subprocess.Popen(BACKEND, cwd=ROOT / "backend", start_new_session=True),
             subprocess.Popen(FRONTEND, cwd=ROOT / "frontend", start_new_session=True)]
    print("backend: http://127.0.0.1:8000/docs   frontend: the address Vite prints (usually http://localhost:5173)", flush=True)
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            _stop(p)
    return max([p.returncode for p in procs if p.returncode and p.returncode > 0], default=0)  # killed by us = ok


if __name__ == "__main__":
    sys.exit(install() if sys.argv[1:] == ["install"] else run())
