import json

import pytest

from swarmd import guard
from swarmd.board import Board
from swarmd.cli import main
from swarmd.critics import MAX_CRITIC_ROUNDS, CritiqueError, Critiques
from swarmd.gate import Gate
from swarmd.models import EventIn, WorkerSpec
from swarmd.runner import Exec, Runner
from swarmd.tasks import Tasks
from test_runner import git, repo  # noqa: F401


class Script:
    """Fake claude. Workers post `done`; critics file the next scripted findings for their persona; verifiers pass."""
    def __init__(self, board, findings):
        self.board, self.findings, self.crit, self.tasks = board, list(findings), Critiques(board), Tasks(board)
        self.calls = []

    def __call__(self, cmd, cwd, env, timeout):
        role, who = env["SWARM_ROLE"], env["SWARM_WORKER"]
        self.calls.append((role, who, cmd[2], cmd, env, cwd))
        if role == "worker":
            self.board.post(EventIn(agent=who, kind="done", body="finished"))
        elif role == "critic" and who.endswith("-correctness") and self.findings:
            for sev, at, reason in self.findings.pop(0):
                self.crit.add("api", who, sev, reason, at)
        elif role == "verifier":
            self.tasks.submit_verdict("api", True, ["ok"], verifier=who)
        return Exec(0, json.dumps({"result": "ok", "total_cost_usd": 0.01, "is_error": False, "permission_denials": []}), "")


def setup(repo, findings):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    ex = Script(board, findings)
    r = Runner(board, repo, executor=ex); r.define(WorkerSpec(name="api", goal="build api", owns=["src/api"]))
    return board, r, ex, Gate(r, critic_model="haiku", personas=("correctness", "security"))


def roles(ex):
    return [(c[0], c[1]) for c in ex.calls]


def test_blocker_goes_back_to_worker_before_verifier_then_passes(repo):
    board, r, ex, gate = setup(repo, [[("blocker", "src/api/a.py:3", "empty input -> IndexError")], []])
    gate.invoke("api", "go")
    seq = roles(ex)
    assert seq[0] == ("worker", "api")
    i_fix = [i for i, x in enumerate(seq) if x == ("worker", "api")][1]
    assert seq.index(("verifier", "verify-api")) > i_fix  # fix happened before any verifier round
    fix_prompt = ex.calls[i_fix][2]
    assert "BLOCKERS" in fix_prompt and "src/api/a.py:3" in fix_prompt and "IndexError" in fix_prompt
    t = Tasks(board).get("api")
    assert t.status == "done" and t.rounds == 0  # critic round did not consume a verifier round
    assert Critiques(board).rounds("api") == 2
    assert any(e.kind == "critique" for e in board.list_events().items)


def test_critics_are_read_only_in_the_workers_worktree_with_the_right_model(repo):
    board, r, ex, gate = setup(repo, [[]])
    gate.invoke("api", "go")
    crit = [c for c in ex.calls if c[0] == "critic"]
    assert {c[1] for c in crit} == {"critic-api-correctness", "critic-api-security"}
    for _, who, prompt, cmd, env, cwd in crit:
        allowed = cmd[cmd.index("--allowedTools") + 1: cmd.index("--session-id")]
        assert allowed == ["Read", "Glob", "Grep"] and env["SWARM_OWNS"] == "" and cwd == repo / ".swarm" / "wt" / "api"
        assert cmd[cmd.index("--model") + 1] == "haiku" and "Base commit:" in prompt


def test_rounds_are_capped_and_remaining_blockers_reach_the_verifier(repo):
    findings = [[("blocker", "a.py:1", f"still broken {i}")] for i in range(5)]
    board, r, ex, gate = setup(repo, findings)
    gate.invoke("api", "go")
    assert Critiques(board).rounds("api") == MAX_CRITIC_ROUNDS
    verifier_prompt = [c[2] for c in ex.calls if c[0] == "verifier"][0]
    assert "Unresolved critic blockers" in verifier_prompt and "still broken 1" in verifier_prompt
    assert Tasks(board).get("api").status == "done"


def test_majors_and_nits_do_not_gate(repo):
    board, r, ex, gate = setup(repo, [[("major", "a.py:9", "no test for error path"), ("nit", "", "naming")]])
    gate.invoke("api", "go")
    assert [x for x in roles(ex) if x[0] == "worker"] == [("worker", "api")]  # worker ran once
    assert Critiques(board).rounds("api") == 1 and len(Critiques(board).list("api")) == 2


def test_critics_off_by_default(repo):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    ex = Script(board, []); r = Runner(board, repo, executor=ex); r.define(WorkerSpec(name="api", goal="g", owns=["src"]))
    Gate(r).invoke("api", "go")
    assert [c[0] for c in ex.calls] == ["worker", "verifier"]




def test_finding_needs_location_and_reason(repo):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t"); c = Critiques(board)
    with pytest.raises(CritiqueError):
        c.add("api", "critic-x", "blocker", "bad", "")              # no file:line
    with pytest.raises(CritiqueError):
        c.add("api", "critic-x", "major", " ", "a.py:1")           # no reason
    with pytest.raises(CritiqueError):
        c.add("api", "critic-x", "huge", "bad", "a.py:1")
    c.add("plan", "plan-critic", "major", "workers a and b both own src/")  # plan findings need no location
    c.add("api", "critic-x", "nit", "naming")


def test_cli_critique_is_critic_only_and_post_rejects_the_kind(repo, monkeypatch):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    monkeypatch.setenv("SWARM_HOME", str(repo)); monkeypatch.chdir(repo)
    monkeypatch.setenv("SWARM_ROLE", "worker")
    assert main(["critique", "api", "blocker", "x", "--at", "a.py:1"]) == 3
    monkeypatch.setenv("SWARM_ROLE", "critic"); monkeypatch.setenv("SWARM_WORKER", "critic-api-security")
    assert main(["critique", "api", "blocker", "x"]) == 2                          # missing --at
    assert main(["critique", "api", "blocker", "SQL built from input", "--at", "a.py:7"]) == 0
    assert main(["critique", "api", "ok", "read the diff, nothing found"]) == 0
    assert main(["post", "critic-api-security", "critique", "sneaky"]) == 2
    assert main(["mention", "api", "hi"]) == 3
    assert len(Critiques(board).blockers("api")) == 1


def test_critic_cannot_write_or_commit(repo):
    env = {"SWARM_WORKER": "critic-api-security", "SWARM_ROLE": "critic", "SWARM_OWNS": "", "SWARM_WORKTREE": str(repo)}
    assert guard.decide({"tool_name": "Edit", "tool_input": {"file_path": str(repo / "a.py")}}, env)["hookSpecificOutput"]["permissionDecision"] == "deny"
    for cmd in ("git commit -m x", "touch a"):
        out = guard.decide({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(repo)}, env)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert guard.decide({"tool_name": "Bash", "tool_input": {"command": "git diff HEAD~1"}, "cwd": str(repo)}, env)["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_mcp_critique_plan(repo):
    from swarmd.mcp_server import SwarmTools
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("build thing")
    ex = Script(board, []); r = Runner(board, repo, executor=ex)
    t = SwarmTools(board, r)
    t.runner.define(WorkerSpec(name="api", goal="g", owns=["src"]))
    assert t.critique_plan("1. api does everything")["ok"]
    out = t.wait_for_workers(["plan-critic"], timeout_s=5)
    assert out["workers"][0]["status"] == "idle"
    p = ex.calls[0][2]
    assert "build thing" in p and "api does everything" in p and "owns src" in p and ex.calls[0][5] == repo
    assert t.swarm_start("again", critics=True)["ok"] and t.gate.critic_model == "sonnet"
