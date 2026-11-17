import os
import subprocess
import sys
import time

import pytest

from swarmd.board import Board
from swarmd.gate import Gate
from swarmd.models import WorkerSpec
from swarmd.monitor import Monitor
from swarmd.runner import Runner
from test_runner import FakeClaude, git, repo  # noqa: F401


@pytest.fixture
def swarm(repo):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    fake = FakeClaude()
    r = Runner(board, repo, executor=fake)
    for n in ("api", "ui"):
        r.define(WorkerSpec(name=n, goal=f"build {n}", owns=[f"src/{n}"]))
    return board, r, fake, Monitor(Gate(r))


def test_peer_mention_is_queued_not_run_inline(swarm):
    board, r, fake, mon = swarm
    out = r.enqueue_mention("ui", "api", "what is the response shape of /todos?")
    assert out == {"ok": True, "queued": "api"} and not fake.calls
    assert r.pending() == ["api"] and r.reg.queue_len(r.reg.get("api")) == 1
    assert any(e.agent == "ui" and e.kind == "mention" and e.body.startswith("@api ") for e in board.list_events().items)


def test_monitor_dispatch_wakes_target_with_merged_messages(swarm):
    board, r, fake, mon = swarm
    r.enqueue_mention("ui", "api", "shape?"); r.enqueue_mention("ui", "api", "and the error format?")
    assert mon.dispatch() == ["api"]; mon.join()
    assert len(fake.calls) == 1 and "shape?" in fake.calls[0]["cmd"][2] and "error format" in fake.calls[0]["cmd"][2]
    assert r.pending() == [] and mon.dispatch() == []


def test_guards(swarm):
    board, r, fake, mon = swarm
    assert "itself" in r.enqueue_mention("api", "api", "x")["error"]
    assert not r.enqueue_mention("api", "ghost", "x")["ok"]
    r.reg.define_verifier(r.reg.get("api"))
    assert "only workers" in r.enqueue_mention("verify-api", "ui", "x")["error"]
    assert "only workers" in r.enqueue_mention("ui", "verify-api", "x")["error"]
    for i in range(Runner.MAX_PEER_MESSAGES):
        assert r.enqueue_mention("ui", "api", f"m{i}")["ok"]
    assert "already sent" in r.enqueue_mention("ui", "api", "one more")["error"]  # ping-pong cap
    assert r.enqueue_mention("api", "ui", "reply")["ok"]  # the other direction has its own cap


def test_dispatch_respects_budget(swarm):
    board, r, fake, mon = swarm
    r.limits.set(max_wakeups=1)
    r.invoke("api", "first")  # uses the only wake-up
    r.enqueue_mention("ui", "api", "late question")
    assert mon.dispatch() == [] and len(fake.calls) == 1


def test_charter_lists_teammates_and_mention_command(swarm):
    board, r, fake, mon = swarm
    c = r.charter(r.reg.get("api"))
    assert "Teammates: ui (owns src/ui" in c and "mention <worker>" in c and "do NOT wait" in c
    solo = Board(r.root / ".swarm" / "solo.db"); solo.start_swarm("s")
    r2 = Runner(solo, r.root, executor=fake); r2.define(WorkerSpec(name="only", goal="g", owns=["x"]))
    assert "Teammates" not in r2.charter(r2.reg.get("only"))


def run_cli(repo, env, *args):
    e = {**os.environ, "SWARM_HOME": str(repo), "PYTHONPATH": os.getcwd(), **env}
    p = subprocess.run([sys.executable, "-m", "swarmd.cli", *args], capture_output=True, text=True, env=e)
    return p.returncode, p.stdout + p.stderr


def test_cli_mention_roles(swarm, repo):
    board, r, fake, mon = swarm
    assert run_cli(repo, {"SWARM_ROLE": "worker", "SWARM_WORKER": "ui"}, "mention", "api", "hello")[0] == 0
    assert r.reg.queue_len(r.reg.get("api")) == 1
    code, out = run_cli(repo, {"SWARM_ROLE": "verifier", "SWARM_WORKER": "verify-api"}, "mention", "api", "x")
    assert code == 3 and "verifiers cannot" in out
    assert run_cli(repo, {"SWARM_ROLE": "worker", "SWARM_WORKER": "ui"}, "mention", "ui", "x")[0] == 2  # self
    assert run_cli(repo, {}, "mention", "ui", "from a human")[0] == 0  # human at a terminal
    assert any(e.agent == "human" for e in board.list_events().items)
