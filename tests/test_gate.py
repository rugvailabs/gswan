import json
import os
import subprocess
import sys

import pytest

from swarmd.board import Board
from swarmd.gate import Gate
from swarmd.models import EventIn, WorkerSpec
from swarmd.runner import Exec, Runner
from swarmd.tasks import MAX_ROUNDS, TaskError, Tasks
from test_runner import git, repo  # noqa: F401


class Scripted:
    """Stands in for `claude -p`: workers post `done`; verifiers apply the next scripted verdict."""
    def __init__(self, board, verdicts, worker_posts_done=True):
        self.board, self.verdicts, self.tasks = board, list(verdicts), Tasks(board)
        self.calls, self.worker_posts_done = [], worker_posts_done

    def __call__(self, cmd, cwd, env, timeout):
        role, who = env["SWARM_ROLE"], env["SWARM_WORKER"]
        self.calls.append((role, who, cmd[2], cmd, env, cwd))
        if role == "worker" and self.worker_posts_done:
            self.board.post(EventIn(agent=who, kind="done", body=f"finished {who}"))
        if role == "verifier":
            v = self.verdicts.pop(0) if self.verdicts else None
            if v:
                self.tasks.submit_verdict(who.removeprefix("verify-"), v[0] == "pass", v[1], verifier=who)
        return Exec(0, json.dumps({"result": "ok", "total_cost_usd": 0.01, "is_error": False, "permission_denials": []}), "")


def setup(repo, verdicts, **kw):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    ex = Scripted(board, verdicts, **kw)
    r = Runner(board, repo, executor=ex); r.define(WorkerSpec(name="api", goal="build api", owns=["src/api"], tools=["Bash(pytest:*)"]))
    return board, r, ex, Gate(r), Tasks(board)


def test_done_is_provisional_then_pass_settles(repo):
    board, r, ex, gate, tasks = setup(repo, [("pass", ["tests green"])])
    assert tasks.get("api").status == "open"
    gate.invoke("api", "go")
    assert tasks.get("api").status == "done" and tasks.unsettled() == []
    assert [c[0] for c in ex.calls] == ["worker", "verifier"]
    assert any(e.kind == "verify" and e.body.startswith("pass api") for e in board.list_events().items)


def test_verifier_is_read_only_and_runs_in_worker_worktree(repo):
    board, r, ex, gate, tasks = setup(repo, [("pass", ["ok"])]); gate.invoke("api", "go")
    role, who, prompt, cmd, env, cwd = ex.calls[1]
    allowed = cmd[cmd.index("--allowedTools") + 1: cmd.index("--session-id")] if "--session-id" in cmd else cmd[cmd.index("--allowedTools") + 1: cmd.index("--resume")]
    assert "Write" not in allowed and "Edit" not in allowed
    assert json.loads(env["SWARM_ALLOW"]) == ["Bash(pytest:*)"] and env["SWARM_ROLE"] == "verifier"  # inherits test cmds
    assert env["SWARM_OWNS"] == "" and who == "verify-api"
    assert cwd == repo / ".swarm" / "wt" / "api"
    assert "Base commit:" in prompt and "finished api" in prompt


def test_fail_goes_back_to_worker_then_passes(repo):
    board, r, ex, gate, tasks = setup(repo, [("fail", ["no tests", "edits README"]), ("pass", ["fixed"])])
    gate.invoke("api", "go")
    assert tasks.get("api").status == "done"
    roles = [(c[0], c[1]) for c in ex.calls]
    assert roles == [("worker", "api"), ("verifier", "verify-api"), ("worker", "api"), ("verifier", "verify-api")]
    fix_prompt = ex.calls[2][2]
    assert "Verification FAILED" in fix_prompt and "1. no tests" in fix_prompt and "2. edits README" in fix_prompt
    assert "Previous failed reasons" in ex.calls[3][2]
    assert r.reg.get("api").turns == 2  # same persistent session resumed for the fix
    assert "--resume" in ex.calls[2][3]


def test_escalates_after_max_rounds(repo):
    board, r, ex, gate, tasks = setup(repo, [("fail", ["bad"])] * (MAX_ROUNDS + 2))
    gate.invoke("api", "go")
    t = tasks.get("api")
    assert t.status == "escalated" and t.rounds == MAX_ROUNDS and tasks.unsettled() == []
    assert any(e.kind == "blocked" and "needs a human" in e.body for e in board.list_events().items)
    assert sum(1 for c in ex.calls if c[0] == "verifier") == MAX_ROUNDS


def test_verifier_without_verdict_escalates(repo):
    board, r, ex, gate, tasks = setup(repo, [])
    gate.invoke("api", "go")
    assert tasks.get("api").status == "escalated"
    assert any("no verdict" in e.body for e in board.list_events().items)


def test_worker_that_never_posts_done_is_not_verified(repo):
    board, r, ex, gate, tasks = setup(repo, [("pass", ["x"])], worker_posts_done=False)
    gate.invoke("api", "go")
    assert tasks.get("api").status == "open" and [c[0] for c in ex.calls] == ["worker"]
    assert len(tasks.unsettled()) == 1  # still blocks the coordinator from finishing


def test_verdict_rules(repo):
    board, r, ex, gate, tasks = setup(repo, [])
    with pytest.raises(TaskError):
        tasks.submit_verdict("api", True, ["x"])  # not under verification yet
    tasks.request_verification("api")
    with pytest.raises(TaskError):
        tasks.submit_verdict("api", False, [])  # fail needs a reason
    assert tasks.submit_verdict("api", False, ["r"]).status == "failed"
    with pytest.raises(TaskError):
        tasks.submit_verdict("api", True, ["x"])  # failed task can't be passed without a new done


def test_verifier_not_flagged_stale(repo):
    board, r, ex, gate, tasks = setup(repo, [("pass", ["ok"])]); gate.invoke("api", "go")
    from datetime import datetime, timedelta, timezone
    names = {s.agent for s in board.stale(1, now=datetime.now(timezone.utc) + timedelta(hours=1))}
    assert "verify-api" not in names and "swarmd" not in names


def run_cli(repo, *args, env=None):
    e = {**os.environ, "SWARM_HOME": str(repo), "PYTHONPATH": os.getcwd(), **(env or {})}
    p = subprocess.run([sys.executable, "-m", "swarmd.cli", *args], capture_output=True, text=True, env=e)
    return p.returncode, p.stdout + p.stderr


def test_cli_verdict_permissions(repo):
    board, r, ex, gate, tasks = setup(repo, []); tasks.request_verification("api")
    code, out = run_cli(repo, "verdict", "api", "pass", "--reason", "x")
    assert code == 3 and "only a verifier" in out  # workers/anyone without the role are refused
    code, out = run_cli(repo, "verdict", "api", "pass", "--reason", "x", env={"SWARM_ROLE": "verifier", "SWARM_WORKER": "verify-ui"})
    assert code == 3 and "may only verify" in out
    code, out = run_cli(repo, "verdict", "api", "pass", "--reason", "ok", env={"SWARM_ROLE": "verifier", "SWARM_WORKER": "verify-api"})
    assert code == 0 and tasks.get("api").status == "done"
    assert "done" in run_cli(repo, "tasks")[1]


# ---- stop hook --------------------------------------------------------------
def test_stop_hook_blocks_until_settled_with_cap(repo):
    from swarmd.stophook import MAX_BLOCKS, decide
    board, r, ex, gate, tasks = setup(repo, [("pass", ["ok"])])
    for _ in range(MAX_BLOCKS):
        out = decide(repo, {})
        assert out and out["decision"] == "block" and "api [open]" in out["reason"]
    assert decide(repo, {}) is None  # cap reached: never trap the session forever
    assert decide(repo, {"SWARM_WORKER": "api"}) is None  # workers are never blocked


def test_stop_hook_allows_when_done_or_no_swarm(repo, tmp_path):
    from swarmd.stophook import decide
    from swarmd import decisions
    board, r, ex, gate, tasks = setup(repo, [("pass", ["ok"])]); gate.invoke("api", "go")
    decisions.record(board, "split", "one worker: the task only touches src/api")
    assert decide(repo, {}) is None
    assert decide(tmp_path, {}) is None  # no db at all


def test_task_transitions_are_tagged_for_the_replay_timeline(repo):
    board, r, ex, gate, tasks = setup(repo, [("fail", ["a"]), ("fail", ["b"])]); gate.invoke("api", "go")
    refs = [x for e in board.list_events(limit=500).items for x in e.refs if x.startswith("task:")]
    assert refs == ["task:api:verifying", "task:api:failed", "task:api:verifying", "task:api:failed", "task:api:escalated"]


def test_passing_task_is_tagged_done(repo):
    board, r, ex, gate, tasks = setup(repo, [("pass", ["ok"])]); gate.invoke("api", "go")
    assert [x for e in board.list_events(limit=500).items for x in e.refs if x.startswith("task:")] == ["task:api:verifying", "task:api:done"]


# ---- required checks: the verifier must run the task's own test command (a real run's verifier ran pytest instead) ----

def test_pass_is_refused_until_required_checks_ran(repo, monkeypatch, capsys):
    from swarmd.cli import main
    from swarmd.tasks import record_run
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    r = Runner(board, repo, executor=Scripted(board, []))
    r.define(WorkerSpec(name="api", goal="g", owns=["src"], tools=["Bash(python3:*)"], checks=["python3 -m unittest discover -s tests"]))
    v = r.reg.define_verifier(r.reg.get("api"))
    assert v.checks == ["python3 -m unittest discover -s tests"]
    tasks = Tasks(board); tasks.request_verification("api"); tasks.set_status("api", "verifying")
    monkeypatch.setenv("SWARM_HOME", str(repo)); monkeypatch.setenv("SWARM_ROLE", "verifier"); monkeypatch.setenv("SWARM_WORKER", "verify-api")
    record_run(board, "verify-api", "python3 -m pytest -q")
    assert main(["verdict", "api", "pass", "--reason", "ok"]) == 2
    assert "python3 -m unittest discover -s tests" in capsys.readouterr().err and tasks.get("api").status == "verifying"
    assert main(["verdict", "api", "fail", "--reason", "broken"]) == 0  # failing never needs the checks
    tasks.request_verification("api"); tasks.set_status("api", "verifying")
    record_run(board, "verify-api", "cd sub && python3  -m unittest discover -s tests -v")
    assert main(["verdict", "api", "pass", "--reason", "ok"]) == 0 and tasks.get("api").status == "done"


def test_checks_from_an_earlier_round_do_not_count(repo):
    from swarmd.tasks import record_run
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    tasks = Tasks(board); tasks.ensure("api", "g")
    record_run(board, "verify-api", "python3 -m pytest")
    tasks.request_verification("api"); tasks.set_status("api", "verifying")
    assert tasks.missing_checks("api", ["python3 -m pytest"], "verify-api") == ["python3 -m pytest"]


def test_guard_records_verifier_commands_only(repo):
    from swarmd import guard
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    wt = str(repo)
    base = {"SWARM_HOME": str(repo), "SWARM_WORKTREE": wt, "SWARM_ALLOW": '["Bash(python3:*)"]'}
    guard.decide({"tool_name": "Bash", "tool_input": {"command": "python3 -m pytest"}, "cwd": wt}, {**base, "SWARM_WORKER": "verify-api", "SWARM_ROLE": "verifier"})
    guard.decide({"tool_name": "Bash", "tool_input": {"command": "python3 -m pytest"}, "cwd": wt}, {**base, "SWARM_WORKER": "api", "SWARM_ROLE": "worker"})
    guard.decide({"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}, "cwd": wt}, {**base, "SWARM_WORKER": "verify-api", "SWARM_ROLE": "verifier"})
    with board._conn() as c:
        rows = [(r["worker"], r["command"]) for r in c.execute("SELECT worker, command FROM ran_commands")]
    assert rows == [("verify-api", "python3 -m pytest")]  # denied commands and other roles are not recorded


def test_verifier_brief_lists_required_checks(repo):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    ex = Scripted(board, [("pass", ["ok"])])
    r = Runner(board, repo, executor=ex)
    r.define(WorkerSpec(name="api", goal="g", owns=["src"], tools=["Bash(python3:*)"], checks=["python3 -m unittest"]))
    Gate(r).invoke("api", "go")
    prompt = next(c[2] for c in ex.calls if c[0] == "verifier")
    assert "Required checks" in prompt and "`python3 -m unittest`" in prompt


def test_integrator_inherits_every_workers_checks(repo):
    from swarmd.integrate import Integrator
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    r = Runner(board, repo, executor=Scripted(board, []))
    r.define(WorkerSpec(name="a", goal="g", owns=["a"], tools=["Bash(python3:*)"], checks=["python3 -m pytest tests/a"]))
    r.define(WorkerSpec(name="b", goal="g", owns=["b"], tools=["Bash(python3:*)"], checks=["python3 -m pytest tests/b", "python3 -m pytest tests/a"]))
    r.ensure_worktree(r.reg.get("a"))  # a worker has run, so there is a base to integrate onto
    integ = Integrator(Gate(r))
    wt, branch = integ.ensure()
    assert integ._integrator(wt, branch).checks == ["python3 -m pytest tests/a", "python3 -m pytest tests/b"]
