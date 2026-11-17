import json
from datetime import datetime, timedelta, timezone

import pytest

from swarmd import cli
from swarmd.board import Board
from swarmd.limits import BudgetExceeded, Limits, LimitsStore
from swarmd.mcp_server import SwarmTools
from swarmd.models import EventIn, WorkerSpec
from swarmd.runner import Runner
from swarmd.workers import WorkerBudgetError
from test_runner import FakeClaude, git, repo  # noqa: F401


def mk(repo, limits=None, **fake_kw):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t", limits=limits)
    return board, Runner(board, repo, executor=FakeClaude(**fake_kw))


def spec(n): return WorkerSpec(name=n, goal="g", owns=[f"src/{n}"])


def test_defaults_and_custom_limits(repo):
    board, r = mk(repo)
    assert LimitsStore(board).get() == Limits()
    board.start_swarm("two", limits={"max_workers": 2, "max_cost_usd": 1.5, "max_wall_s": None})
    got = LimitsStore(board).get()
    assert (got.max_workers, got.max_cost_usd, got.max_wall_s) == (2, 1.5, 3600)


def test_worker_limit_enforced_but_verifier_and_integrator_exempt(repo):
    board, r = mk(repo, limits={"max_workers": 2})
    r.define(spec("a")); r.define(spec("b"))
    with pytest.raises(WorkerBudgetError, match="worker limit"):
        r.define(spec("c"))
    r.reg.define(spec("integrator"), exempt_limit=True)  # exempt path used by the integrator
    r.reg.define_verifier(r.reg.get("a"))  # verifiers are not counted as workers
    assert LimitsStore(board).usage()["workers"] == 3


def test_wakeup_limit_blocks_turns_and_keeps_messages_queued(repo):
    board, r = mk(repo, limits={"max_wakeups": 2}); r.define(spec("a"))
    assert r.invoke("a", "one").ok and r.invoke("a", "two").ok
    res = r.invoke("a", "three")
    assert not res.ok and "budget exhausted" in res.text and "wake-ups 2/2" in res.text
    assert r.reg.queue_len(r.reg.get("a")) == 1  # not lost
    blocked = [e for e in board.list_events().items if e.kind == "blocked" and "budget" in e.body]
    assert len(blocked) == 1
    r.invoke("a", "four")  # same refusal again must not spam the board
    assert len([e for e in board.list_events().items if e.kind == "blocked" and "budget" in e.body]) == 1
    LimitsStore(board).set(max_wakeups=10)  # a human raises it; queued work resumes
    assert r.invoke("a", "five").ok and r.reg.get("a").turns == 3  # 'three'+'four'+'five' merged into ONE turn


def test_cost_and_wall_limits(repo):
    board, r = mk(repo, limits={"max_cost_usd": 0.015}); r.define(spec("a"))
    assert r.invoke("a", "x").ok  # fake charges $0.01
    assert r.invoke("a", "y").ok  # 0.01 < 0.015
    assert not r.invoke("a", "z").ok  # 0.02 >= 0.015
    store = LimitsStore(board)
    with pytest.raises(BudgetExceeded, match="wall time"):
        store.set(max_cost_usd=100); store.check_turn(now=datetime.now(timezone.utc) + timedelta(hours=2))


def test_set_validation(repo):
    board, r = mk(repo); st = LimitsStore(board)
    with pytest.raises(ValueError, match="unknown limit"):
        st.set(max_banana=1)
    with pytest.raises(ValueError, match="positive"):
        st.set(max_workers=0)


def test_mcp_tools_expose_limits(repo):
    board = Board(repo / ".swarm" / "swarm.db")
    t = SwarmTools(board, Runner(board, repo, executor=FakeClaude()))
    out = t.swarm_start("task", max_workers=1, max_cost_usd=3)
    assert out["limits"]["max_workers"] == 1 and out["limits"]["max_cost_usd"] == 3.0 and out["usage"]["workers"] == 0
    assert t.define_worker("a", "g", ["src/a"])["ok"]
    over = t.define_worker("b", "g", ["src/b"])
    assert over["ok"] is False and "worker limit" in over["error"]
    st = t.swarm_status()
    assert st["usage"]["workers"] == 1 and st["limits"]["max_workers"] == 1


def test_cli_limits(repo, monkeypatch, capsys):
    monkeypatch.setenv("SWARM_HOME", str(repo))
    assert cli.main(["init", "t"]) == 0; capsys.readouterr()
    assert cli.main(["limits", "--set", "max_workers=9", "max_cost_usd=2.5"]) == 0
    out = capsys.readouterr().out
    assert "max_workers    0 / 9" in out and "max_cost_usd   0 / 2.5" in out
    assert cli.main(["limits", "--set", "bogus=1"]) == 2
