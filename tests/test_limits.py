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
    board.start_swarm("two", limits={"max_workers": 2, "max_tokens": 150000, "max_wall_s": None})
    got = LimitsStore(board).get()
    assert (got.max_workers, got.max_tokens, got.max_wall_s) == (2, 150000, 3600)


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


def test_token_and_wall_limits(repo):
    board, r = mk(repo, limits={"max_tokens": 1500}); r.define(spec("a"))
    first = r.invoke("a", "x")  # the fake uses 100 input + 900 cache-write + 50 output = 1050 counted; 4000 cache-read is not
    assert first.ok and first.tokens == {"input": 1000, "output": 50, "cached": 4000, "total": 1050}
    assert r.invoke("a", "y").ok  # 1050 < 1500
    assert not r.invoke("a", "z").ok  # 2100 >= 1500
    store = LimitsStore(board)
    assert store.usage()["tokens"] == 2100 and store.usage()["tokens_cached"] == 8000
    with pytest.raises(BudgetExceeded, match="tokens"):
        store.check_turn()
    with pytest.raises(BudgetExceeded, match="wall time"):
        store.set(max_tokens=10_000_000); store.check_turn(now=datetime.now(timezone.utc) + timedelta(hours=2))


def test_set_validation(repo):
    board, r = mk(repo); st = LimitsStore(board)
    with pytest.raises(ValueError, match="unknown limit"):
        st.set(max_banana=1)
    with pytest.raises(ValueError, match="positive"):
        st.set(max_workers=0)


def test_mcp_tools_expose_limits(repo):
    board = Board(repo / ".swarm" / "swarm.db")
    t = SwarmTools(board, Runner(board, repo, executor=FakeClaude()))
    out = t.swarm_start("task", max_workers=1, max_tokens=500000)
    assert out["limits"]["max_workers"] == 1 and out["limits"]["max_tokens"] == 500000 and out["usage"]["workers"] == 0
    assert t.define_worker("a", "g", ["src/a"])["ok"]
    over = t.define_worker("b", "g", ["src/b"])
    assert over["ok"] is False and "worker limit" in over["error"]
    st = t.swarm_status()
    assert st["usage"]["workers"] == 1 and st["limits"]["max_workers"] == 1


def test_cli_limits(repo, monkeypatch, capsys):
    monkeypatch.setenv("SWARM_HOME", str(repo))
    assert cli.main(["init", "t"]) == 0; capsys.readouterr()
    assert cli.main(["limits", "--set", "max_workers=9", "max_tokens=2500000"]) == 0
    out = capsys.readouterr().out
    assert "max_workers    0 / 9" in out and "max_tokens     0 / 2500000" in out
    assert cli.main(["limits", "--set", "bogus=1"]) == 2


def test_tokens_parse_view_and_format():
    from swarmd import tokens
    used = tokens.parse_usage({"usage": {"input_tokens": 2, "output_tokens": 4, "cache_creation_input_tokens": 24099, "cache_read_input_tokens": 12112, "iterations": []}})
    assert used == {"tokens_in": 2, "tokens_out": 4, "tokens_cache_write": 24099, "tokens_cache_read": 12112}
    assert tokens.view(**used) == {"input": 24101, "output": 4, "cached": 12112, "total": 24105}
    assert tokens.parse_usage({}) == tokens.ZERO and tokens.parse_usage({"usage": {"input_tokens": "x", "output_tokens": None}}) == tokens.ZERO
    assert [tokens.fmt(n) for n in (0, 999, 1000, 1234, 24101, 999_949, 1_000_000, 1_234_567)] == ["0", "999", "1k", "1.2k", "24.1k", "999.9k", "1M", "1.23M"]
    assert tokens.line({"input": 24101, "output": 4, "cached": 0, "total": 24105}) == "24.1k in / 4 out"


def test_old_database_with_dollar_budget_is_migrated(tmp_path):
    import sqlite3
    db = tmp_path / "swarm.db"
    c = sqlite3.connect(db)
    c.executescript("""
      CREATE TABLE swarms (id TEXT PRIMARY KEY, task TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
      CREATE TABLE swarm_limits (swarm_id TEXT PRIMARY KEY, max_workers INTEGER NOT NULL, max_wakeups INTEGER NOT NULL,
        max_wall_s INTEGER NOT NULL, max_cost_usd REAL NOT NULL);
      INSERT INTO swarms VALUES ('swm_old', 'old run', 'active', '2026-01-01T00:00:00.000000Z');
      INSERT INTO swarm_limits VALUES ('swm_old', 4, 20, 600, 10.0);
    """); c.commit(); c.close()
    board = Board(db)
    st = LimitsStore(board)
    assert st.get() == Limits(max_workers=4, max_wakeups=20, max_wall_s=600, max_tokens=3_000_000)
    assert st.set(max_tokens=5_000_000).max_tokens == 5_000_000 and st.usage()["tokens"] == 0
    board.start_swarm("new run", limits={"max_tokens": 1000})  # the old NOT NULL dollar column must not break inserts
    assert LimitsStore(board).get().max_tokens == 1000
