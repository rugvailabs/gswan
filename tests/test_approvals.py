"""Step 2: denied commands become questions for a human; budgets pause work instead of failing it."""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from swarmd import guard
from swarmd.api import create_app
from swarmd.approvals import MAX_PENDING, ApprovalError, Approvals, program_of
from swarmd.board import Board
from swarmd.cli import main
from swarmd.gate import Gate
from swarmd.limits import LimitsStore
from swarmd.mcp_server import SwarmTools
from swarmd.models import EventIn, WorkerSpec
from swarmd.monitor import Monitor
from swarmd.runner import Exec, Runner
from swarmd.tasks import Tasks
from test_runner import git, repo  # noqa: F401


class Script:
    """Fake claude: workers post `done` (and record the prompt); verifiers pass."""
    def __init__(self, board):
        self.board, self.tasks, self.calls = board, Tasks(board), []

    def __call__(self, cmd, cwd, env, timeout):
        role, who = env["SWARM_ROLE"], env["SWARM_WORKER"]
        self.calls.append((role, who, cmd[2]))
        if role == "worker":
            self.board.post(EventIn(agent=who, kind="done", body="finished"))
        elif role == "verifier":
            self.tasks.submit_verdict(who.removeprefix("verify-"), True, ["ok"], verifier=who)
        return Exec(0, json.dumps({"result": "ok", "is_error": False, "permission_denials": [],
                                   "usage": {"input_tokens": 10, "output_tokens": 5}}), "")


def setup(repo, limits=None):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t", limits=limits)
    ex = Script(board)
    r = Runner(board, repo, executor=ex)
    r.define(WorkerSpec(name="api", goal="g", owns=["src"], tools=["Bash(python3:*)"]))
    return board, r, ex


def env_for(repo, worker="api", role="worker"):
    wt = str(repo)
    return {"SWARM_HOME": str(repo), "SWARM_WORKTREE": wt, "SWARM_ALLOW": '["Bash(python3:*)"]', "SWARM_WORKER": worker, "SWARM_ROLE": role}


def bash(repo, cmd, **kw):
    out = guard.decide({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(repo)}, env_for(repo, **kw))
    return out["hookSpecificOutput"]["permissionDecision"], out["hookSpecificOutput"]["permissionDecisionReason"]


# ---- requests ---------------------------------------------------------------------------------------------------

def test_a_denied_worker_command_files_one_request(repo):
    board, r, _ = setup(repo)
    d, why = bash(repo, "npm test")
    assert d == "deny" and "request apr_" in why and "Do NOT wait" in why
    [a] = Approvals(board).list("pending")
    assert (a.worker, a.command, a.program) == ("api", "npm test", "npm")
    assert any(e.kind == "approval" and a.id in e.body for e in board.list_events().items)
    d, why = bash(repo, "npm   test")  # same command again: no second request
    assert d == "deny" and a.id in why and "already open" in why and len(Approvals(board).list()) == 1


def test_read_only_roles_never_file_requests(repo):
    board, r, _ = setup(repo)
    for role in ("verifier", "critic", "researcher"):
        d, why = bash(repo, "npm test", worker=f"x-{role}", role=role)
        assert d == "deny" and "request" not in why
    assert Approvals(board).list() == []


def test_pending_requests_are_capped(repo):
    board, r, _ = setup(repo)
    for i in range(MAX_PENDING):
        bash(repo, f"tool{i} run")
    d, why = bash(repo, "another run")
    assert d == "deny" and "post `blocked`" in why and len(Approvals(board).list("pending")) == MAX_PENDING


def test_program_is_only_offered_for_a_missing_grant():
    assert program_of("PYTHONPATH=src npm test", "`npm` is not allowed. Granted commands: none") == "npm"
    assert program_of("ls > out.txt", "redirections are not allowed (only 2>&1 and >/dev/null)") is None


# ---- decisions --------------------------------------------------------------------------------------------------

def test_allow_lets_exactly_that_command_through(repo):
    board, r, _ = setup(repo)
    bash(repo, "ls > out.txt")
    a = Approvals(board).list("pending")[0]
    with pytest.raises(ApprovalError, match="not about a missing program"):
        Approvals(board).decide(a.id, "program")
    Approvals(board).decide(a.id, "allow", "fine once")
    assert bash(repo, "ls  >  out.txt")[0] == "allow"
    assert bash(repo, "ls > other.txt")[0] == "deny"
    assert bash(repo, "ls > out.txt", worker="ui")[0] == "deny"  # approvals are per worker
    with pytest.raises(ApprovalError, match="already allowed"):
        Approvals(board).decide(a.id, "deny")


def test_program_grant_applies_at_once_and_to_its_verifier(repo):
    board, r, _ = setup(repo)
    r.reg.define_verifier(r.reg.get("api"))
    bash(repo, "npm test")
    a = Approvals(board).list("pending")[0]
    Approvals(board).decide(a.id, "program")
    assert "Bash(npm:*)" in r.reg.get("api").tools and "Bash(npm:*)" in r.reg.get("verify-api").tools
    assert bash(repo, "npm run lint")[0] == "allow"  # same turn: the guard reads grants from the db


def test_deny_is_remembered(repo):
    board, r, _ = setup(repo)
    bash(repo, "curl https://example.com")
    a = Approvals(board).list("pending")[0]
    Approvals(board).decide(a.id, "deny", "no network")
    d, why = bash(repo, "curl https://example.com")
    assert d == "deny" and "already denied" in why and "no network" in why and len(Approvals(board).list()) == 1
    assert "DENIED" in Approvals.message(Approvals(board).get(a.id)) and "no network" in Approvals.message(Approvals(board).get(a.id))


def test_deciding_wakes_the_worker_with_the_answer(repo):
    board, r, ex = setup(repo)
    tools = SwarmTools(board, r, Gate(r))
    bash(repo, "npm test")
    a = Approvals(board).list("pending")[0]
    assert tools.decide_approval(a.id, "allow")["ok"]
    tools.threads["api"].join(5)
    worker_prompts = [p for role, who, p in ex.calls if who == "api"]
    assert worker_prompts and "approved your command" in worker_prompts[-1] and "npm test" in worker_prompts[-1]
    assert tools.decide_approval(a.id, "deny")["ok"] is False


def test_status_and_wait_show_pending_requests(repo):
    board, r, _ = setup(repo)
    tools = SwarmTools(board, r, Gate(r))
    bash(repo, "npm test")
    assert [x["command"] for x in tools.swarm_status()["pendingApprovals"]] == ["npm test"]
    assert "npm test" in tools.wait_for_workers(timeout_s=1)["pendingApprovals"][0]


def test_monitor_sees_a_worker_waiting_on_a_human_not_a_forgetful_one(repo):
    board, r, _ = setup(repo)
    r.invoke("api", "go")  # one turn ran; the task is not verified, so it looks like "forgot to report"
    Tasks(board).set_status("api", "open")
    bash(repo, "npm test")
    m = Monitor(Gate(r))
    f = m.diagnose(r.reg.get("api"), __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
    assert f.state == "waiting" and "approval" in f.detail


def test_cli_answers_are_human_only(repo, monkeypatch, capsys):
    board, r, _ = setup(repo)
    bash(repo, "npm test")
    a = Approvals(board).list("pending")[0]
    monkeypatch.setenv("SWARM_HOME", str(repo))
    monkeypatch.setenv("SWARM_ROLE", "worker")
    assert main(["approve", a.id]) == 3
    assert main(["limits", "--set", "max_tokens=9000000"]) == 3  # agents cannot raise their own budget
    monkeypatch.delenv("SWARM_ROLE")
    assert main(["approvals"]) == 0 and "npm test" in capsys.readouterr().out
    assert main(["approve", a.id, "--program", "--note", "ok"]) == 0
    assert Approvals(board).get(a.id).decision == "program"
    assert r.reg.queue_len(r.reg.get("api")) == 1  # the monitor wakes it


def test_api_lists_and_decides(repo):
    board, r, _ = setup(repo)
    tools = SwarmTools(board, r, Gate(r))
    c = TestClient(create_app(board, "tok", tools, ui_dir=None)); h = {"Authorization": "Bearer tok"}
    bash(repo, "npm test")
    assert c.get("/approvals").status_code == 401
    [a] = c.get("/approvals", headers=h).json()["approvals"]
    assert a["command"] == "npm test" and a["program"] == "npm"
    assert c.post(f"/approvals/{a['id']}", json={"decision": "maybe"}, headers=h).status_code == 422
    assert c.post(f"/approvals/{a['id']}", json={"decision": "deny", "note": "no"}, headers=h).json()["approval"]["status"] == "denied"
    assert c.post(f"/approvals/{a['id']}", json={"decision": "allow"}, headers=h).status_code == 409
    assert c.get("/approvals?status=all", headers=h).json()["approvals"][0]["note"] == "no"


# ---- pause / resume ---------------------------------------------------------------------------------------------

def test_budget_pauses_verification_instead_of_escalating_then_resumes(repo):
    board, r, ex = setup(repo, limits={"max_wakeups": 1})  # the worker's turn uses the only wake-up
    gate = Gate(r)
    tools = SwarmTools(board, r, gate)
    gate.invoke("api", "go")
    t = Tasks(board).get("api")
    assert t.status == "awaiting_verification"  # not escalated
    assert [p["worker"] for p in gate.paused()] == ["api"]
    assert not [c for c in ex.calls if c[0] == "verifier"]  # no verifier turn was attempted past the budget
    pause = tools.swarm_status()["pause"]
    assert pause["paused"] and pause["agents"] == ["api"] and "wake-ups" in pause["reason"]
    assert any(e.kind == "blocked" and e.body.startswith("paused:") for e in board.list_events().items)
    assert tools.resume() == []  # still over budget
    out = tools.set_limits(max_wakeups=10)
    assert out["ok"] and out["resumed"] == ["api"]
    for _ in range(50):
        if Tasks(board).get("api").status == "done":
            break
        time.sleep(0.1)
    assert Tasks(board).get("api").status == "done" and gate.paused() == []
    assert any("resuming verification of api" in e.body for e in board.list_events().items)


def test_queued_messages_wait_while_paused_and_wait_for_workers_returns(repo):
    board, r, ex = setup(repo, limits={"max_wakeups": 1})
    tools = SwarmTools(board, r, Gate(r))
    r.invoke("api", "first")  # uses the budget
    r.invoke("api", "second")  # stays queued
    assert r.reg.queue_len(r.reg.get("api")) == 1
    w = tools.wait_for_workers(["api"], timeout_s=2)
    assert w["timedOut"] is False and w["paused"]["agents"] == ["api"]  # paused is not busy: no endless waiting
    LimitsStore(board).set(max_wakeups=5)
    assert "api" in tools.resume()
    tools.threads["api"].join(5)
    assert r.reg.queue_len(r.reg.get("api")) == 0 and [p for _, who, p in ex.calls if who == "api"][-1] == "second"


def test_monitor_resumes_paused_work_after_a_cli_raise(repo):
    board, r, ex = setup(repo, limits={"max_wakeups": 1})
    gate = Gate(r)
    gate.invoke("api", "go")
    assert gate.paused()
    LimitsStore(board).set(max_wakeups=10)  # what `swarm limits --set` does from another process
    Monitor(gate).step()
    for _ in range(50):
        if Tasks(board).get("api").status == "done":
            break
        time.sleep(0.1)
    assert Tasks(board).get("api").status == "done"


def test_budget_mid_verifier_turn_pauses_and_drops_the_stale_brief(repo):
    board, r, ex = setup(repo, limits={"max_wakeups": 2})
    gate = Gate(r)
    calls = {"n": 0}
    real = r.limits.check_turn

    def flaky():  # passes the gate's pre-check, then the verifier's own turn hits the budget
        calls["n"] += 1
        if calls["n"] == 3:
            from swarmd.limits import BudgetExceeded
            raise BudgetExceeded("swarm budget exhausted: tokens 10/10")
        return real()
    r.limits.check_turn = flaky
    gate.invoke("api", "go")
    assert Tasks(board).get("api").status == "awaiting_verification" and [p["worker"] for p in gate.paused()] == ["api"]
    assert r.reg.queue_len(r.reg.get("verify-api")) == 0
