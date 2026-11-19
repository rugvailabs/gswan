import json
import os
import subprocess
import sys

import pytest

from swarmd import guard
from swarmd.board import Board
from swarmd.cli import main
from swarmd.gate import Gate
from swarmd.models import EventIn, WorkerSpec
from swarmd.runner import Exec, Runner
from swarmd.tasks import Tasks
from swarmd.workers import WorkerBudgetError, WorkerError
from test_runner import git, repo  # noqa: F401


class Fake:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, cwd, env, timeout):
        self.calls.append((cmd, cwd, env))
        return Exec(0, json.dumps({"result": "found it", "total_cost_usd": 0.01, "is_error": False, "permission_denials": []}), "")


def setup(repo):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    ex = Fake(); r = Runner(board, repo, executor=ex)
    return board, r, ex


def test_researcher_is_read_only_web_capable_and_runs_in_project_root(repo):
    board, r, ex = setup(repo)
    w = r.define_researcher("docs", "what does lib X 3.0 change?")
    res = Gate(r).invoke("docs", "go")
    assert res.ok
    cmd, cwd, env = ex.calls[0]
    allowed = cmd[cmd.index("--allowedTools") + 1: cmd.index("--session-id")]
    assert allowed == ["Read", "Glob", "Grep", "WebSearch", "WebFetch"]
    assert cwd == repo and env["SWARM_ROLE"] == "researcher" and env["SWARM_OWNS"] == ""
    assert "READ-ONLY" in cmd[cmd.index("--append-system-prompt") + 1]
    # no task, no verifier, no worktree, not counted as a worker
    assert [c[2]["SWARM_ROLE"] for c in ex.calls] == ["researcher"]
    assert Tasks(board).list() == [] and r.reg.list() == [] and r.limits.usage()["workers"] == 0
    assert r.reg.list_researchers()[0].name == "docs" and not (repo / ".swarm" / "wt" / "docs").exists()


def test_researcher_cannot_write_or_run_mutating_bash(repo):
    env = {"SWARM_WORKER": "docs", "SWARM_ROLE": "researcher", "SWARM_OWNS": "", "SWARM_WORKTREE": str(repo)}
    out = guard.decide({"tool_name": "Write", "tool_input": {"file_path": str(repo / "x.py")}}, env)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    for cmd in ("mkdir -p out", "touch a", "git commit -m x", "git add ."):
        out = guard.decide({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(repo)}, env)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny", cmd
    out = guard.decide({"tool_name": "Bash", "tool_input": {"command": "git log --oneline"}, "cwd": str(repo)}, env)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_researcher_cap_and_duplicate(repo):
    board, r, ex = setup(repo)
    for i in range(r.reg.MAX_RESEARCHERS):
        r.define_researcher(f"r{i}", "q")
    with pytest.raises(WorkerBudgetError):
        r.define_researcher("one-more", "q")
    with pytest.raises(WorkerError):
        r.define_researcher("r0", "q")


def test_research_post_needs_a_source_and_only_researchers_post(repo, monkeypatch, capsys):
    board, r, ex = setup(repo)
    monkeypatch.setenv("SWARM_HOME", str(repo)); monkeypatch.chdir(repo)
    monkeypatch.setenv("SWARM_ROLE", "researcher")
    assert main(["post", "docs", "research", "X changed in 3.0"]) == 2           # no URL
    assert main(["post", "docs", "research", 'X changed | https://x.dev/changelog | "removed Y"']) == 0
    assert main(["post", "docs", "research", "UNVERIFIED: maybe Z"]) == 0
    monkeypatch.setenv("SWARM_ROLE", "worker")
    assert main(["post", "api", "research", "https://x.dev"]) == 3                # workers cannot
    kinds = [e.kind for e in board.list_events().items]
    assert kinds.count("research") == 2
    monkeypatch.setenv("SWARM_ROLE", "researcher")
    assert main(["mention", "api", "hi"]) == 3


def test_mcp_define_researcher_and_wait_includes_it(repo):
    from swarmd.mcp_server import SwarmTools
    board, r, ex = setup(repo)
    t = SwarmTools(board, r)
    assert t.define_researcher("docs", "q")["ok"]
    assert t.invoke_worker("docs", "go")["ok"]
    out = t.wait_for_workers(timeout_s=5)
    assert [w["name"] for w in out["workers"]] == ["docs"] and out["workers"][0]["status"] == "idle"
    assert not t.define_researcher("docs", "q")["ok"]
