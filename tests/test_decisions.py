import pytest

from swarmd import decisions, report
from swarmd.critics import Critiques
from swarmd.mcp_server import SwarmTools
from swarmd.models import EventIn
from swarmd.stophook import decide
from test_gate import setup
from test_runner import git, repo  # noqa: F401

WHY = "api and ui share no files, so they can run in parallel"


def tags(board):
    return [o.tag for o in decisions.missing(board)]


def test_split_is_owed_once_workers_exist_and_settled_by_a_real_reason(repo):
    board, r, ex, gate, tasks = setup(repo, [])
    assert tags(board) == ["[split]"]
    board.post(EventIn(agent="coordinator", kind="decision", body="we split it"))          # untagged
    board.post(EventIn(agent="coordinator", kind="decision", body="[split] because"))      # not a reason
    board.post(EventIn(agent="api", kind="decision", body=f"[split] {WHY}"))               # a worker cannot explain for the coordinator
    assert tags(board) == ["[split]"]
    decisions.record(board, "split", WHY)
    assert tags(board) == []


def test_record_validates(repo):
    board, *_ = setup(repo, [])
    for args in (("bogus", WHY), ("split", "ok"), ("escalation", WHY), ("critic", WHY)):
        with pytest.raises(decisions.DecisionError):
            decisions.record(board, *args)
    assert decisions.record(board, "escalation", WHY, "api").body.startswith("[escalation api] ")


def test_escalation_needs_a_decision_made_after_it(repo):
    board, r, ex, gate, tasks = setup(repo, [("fail", ["a"]), ("fail", ["b"])])
    decisions.record(board, "split", WHY)
    decisions.record(board, "escalation", "written before anything was escalated", "api")  # too early to explain it
    gate.invoke("api", "go")
    assert tasks.get("api").status == "escalated" and tags(board) == ["[escalation api]"]
    decisions.record(board, "escalation", "told the user; api needs a schema they must choose", "api")
    assert tags(board) == []


def test_open_critic_blockers_need_a_decision(repo):
    board, r, ex, gate, tasks = setup(repo, [("pass", ["ok"])])
    decisions.record(board, "split", WHY)
    crit = Critiques(board)
    crit.begin_round("plan"); crit.add("plan", "plan-critic", "blocker", "api and ui both write src/shared")
    crit.begin_round("api"); crit.add("api", "critic-api-security", "blocker", "SQL injection", "a.py:3")
    assert tags(board) == ["[critic plan]"]  # api is not accepted yet, so nothing was overridden
    gate.invoke("api", "go")
    assert tasks.get("api").status == "done" and tags(board) == ["[critic plan]", "[critic api]"]
    decisions.record(board, "critic", "moved src/shared to api only; ui imports it", "plan")
    decisions.record(board, "critic", "verifier confirmed the query is parameterised; finding was wrong", "api")
    assert tags(board) == []
    crit.begin_round("plan"); crit.add("plan", "plan-critic", "blocker", "goal of ui cannot be verified")
    assert tags(board) == ["[critic plan]"]  # a new round of blockers needs a new explanation


def test_merge_and_stop_refuse_until_explained(repo):
    board, r, ex, gate, tasks = setup(repo, [("pass", ["ok"])])
    t = SwarmTools(board, r, gate)
    gate.invoke("api", "go")
    out = t.merge_workers()
    assert out["ok"] is False and out["missingDecisions"][0]["tag"] == "[split]" and "record_decision" in out["error"]
    assert t.swarm_status()["missingDecisions"][0]["topic"] == "split"
    stop = decide(repo, {})
    assert stop["decision"] == "block" and "[split]" in stop["reason"] and "unverified" not in stop["reason"]
    assert "[split]" in report.markdown(report.build(board, repo))
    assert t.record_decision("split", "x")["ok"] is False
    assert t.record_decision("split", WHY)["stillMissing"] == []
    assert t.merge_workers()["ok"] is True and decide(repo, {}) is None
    md = report.markdown(report.build(board, repo))
    assert f"[split] {WHY}" in md and "## Decisions the coordinator has NOT explained\n- none" in md
