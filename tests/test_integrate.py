import json
import subprocess

import pytest

from swarmd.board import Board
from swarmd.gate import Gate
from swarmd.integrate import IntegrationError, Integrator
from swarmd.models import EventIn, WorkerSpec
from swarmd.runner import Exec, Runner
from swarmd.tasks import Tasks
from test_runner import git, repo  # noqa: F401

RESULT = json.dumps({"result": "ok", "total_cost_usd": 0.01, "is_error": False, "permission_denials": []})


def sh(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True)


class Team:
    """Scripted claude: workers write+commit files and post done; verifier passes; integrator resolves conflicts."""
    def __init__(self, board, files, resolve=None):
        self.board, self.files, self.resolve, self.calls = board, files, resolve or {}, []

    def __call__(self, cmd, cwd, env, timeout):
        role, who = env["SWARM_ROLE"], env["SWARM_WORKER"]
        self.calls.append((role, who))
        if role == "verifier":
            Tasks(self.board).submit_verdict(who.removeprefix("verify-"), True, ["ok"], verifier=who)
        elif who == "integrator":
            for path, text in self.resolve.items():
                (cwd / path).write_text(text)
                sh(cwd, "add", path)
            sh(cwd, "-c", "user.name=i", "-c", "user.email=i@i", "commit", "--no-edit", "-q")
            self.board.post(EventIn(agent=who, kind="done", body="resolved conflicts"))
        elif who in self.files:
            for path, text in self.files[who].items():
                (cwd / path).parent.mkdir(parents=True, exist_ok=True)
                (cwd / path).write_text(text)
                sh(cwd, "add", path)
            sh(cwd, "-c", "user.name=w", "-c", "user.email=w@w", "commit", "-qm", f"{who} work")
            self.board.post(EventIn(agent=who, kind="done", body="finished"))
        return Exec(0, RESULT, "")


def setup(repo, files, resolve=None, workers=("api", "ui")):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    team = Team(board, files, resolve)
    r = Runner(board, repo, executor=team)
    for n in workers:
        r.define(WorkerSpec(name=n, goal=f"build {n}", owns=[f"src/{n}"]))
    gate = Gate(r)
    return board, r, team, gate, Integrator(gate)


def test_clean_merge_of_independent_workers(repo):
    board, r, team, gate, integ = setup(repo, {"api": {"src/api/a.py": "A\n"}, "ui": {"src/ui/u.py": "U\n"}})
    for n in ("api", "ui"):
        gate.invoke(n, "go")
    out = integ.merge_ready()
    assert out["ok"] and out["merged"] == ["api", "ui"] and out["allMerged"] and not out["conflicts"] and not out["pending"]
    wt = integ.paths()[0]
    assert (wt / "src/api/a.py").exists() and (wt / "src/ui/u.py").exists()
    assert "swarm integrate --apply" in out["applyHint"]
    assert integ.merge_ready()["merged"] == ["api", "ui"]  # idempotent
    assert not (repo / "src").exists()  # the user's checked-out branch is untouched


def test_unfinished_workers_are_pending_not_merged(repo):
    board, r, team, gate, integ = setup(repo, {"api": {"src/api/a.py": "A\n"}})
    gate.invoke("api", "go")
    r.invoke("ui", "go")  # ui never commits/posts done -> task stays open
    out = integ.merge_ready()
    assert out["merged"] == ["api"] and out["pending"] == [{"worker": "ui", "task": "open"}] and not out["allMerged"]


def test_no_workers_run_is_a_clear_error(repo):
    board, r, team, gate, integ = setup(repo, {})
    with pytest.raises(IntegrationError, match="nothing to integrate"):
        integ.merge_ready()


def test_conflict_goes_to_integrator_then_merge_continues(repo):
    shared = "shared.txt"
    board, r, team, gate, integ = setup(
        repo, {"api": {shared: "from api\n"}, "ui": {shared: "from ui\n"}, "db": {"src/db/d.py": "D\n"}},
        resolve={shared: "from api\nfrom ui\n"}, workers=("api", "ui", "db"))
    for n in ("api", "ui", "db"):
        gate.invoke(n, "go")
    first = integ.merge_ready()
    assert first["merged"] == ["api", "db"] and first["conflicts"] == [{"worker": "ui", "files": [shared]}] and first["integratorStarted"]
    integ.last_thread.join(30)
    assert ("worker", "integrator") in team.calls and ("verifier", "verify-integrator") in team.calls
    assert Tasks(board).get("integrator").status == "done"  # the integrator is verified like any worker
    wt = integ.paths()[0]
    assert (wt / shared).read_text() == "from api\nfrom ui\n" and not integ._merging(wt)
    second = integ.merge_ready()
    assert second["allMerged"] and set(second["merged"]) == {"api", "ui", "db"} and (wt / "src/db/d.py").exists()


def test_conflict_without_auto_resolve_only_reports(repo):
    board, r, team, gate, integ = setup(repo, {"api": {"x.txt": "1\n"}, "ui": {"x.txt": "2\n"}})
    for n in ("api", "ui"):
        gate.invoke(n, "go")
    out = integ.merge_ready(auto_resolve=False)
    assert out["conflicts"][0]["files"] == ["x.txt"] and not out["integratorStarted"]
    again = integ.merge_ready(auto_resolve=False)  # still mid-merge: reports instead of piling on
    assert again["resolving"] and again["conflicts"][0]["files"] == ["x.txt"]


def test_apply_merges_into_current_branch_and_refuses_dirty_tree(repo):
    board, r, team, gate, integ = setup(repo, {"api": {"src/api/a.py": "A\n"}, "ui": {"src/ui/u.py": "U\n"}})
    for n in ("api", "ui"):
        gate.invoke(n, "go")
    integ.merge_ready()
    (repo / "README").write_text("dirty")  # tracked file modified
    assert "uncommitted" in integ.apply()["error"]
    sh(repo, "checkout", "--", "README")
    assert integ.apply()["ok"] and (repo / "src/api/a.py").exists() and (repo / "src/ui/u.py").exists()


def test_verify_integration_runs_verifier_over_the_merged_branch(repo):
    board, r, team, gate, integ = setup(repo, {"api": {"src/api/a.py": "A\n"}, "ui": {"src/ui/u.py": "U\n"}})
    for n in ("api", "ui"):
        gate.invoke(n, "go")
    assert integ.merge_ready()["allMerged"]
    integ.verify_integration().join(30)
    assert Tasks(board).get("integrator").status == "done"
    assert ("verifier", "verify-integrator") in team.calls
    ev = board.list_events().items
    assert any(e.agent == "integrator" and e.kind == "done" and "Integration branch" in e.body for e in ev)
    assert any(e.agent == "verify-integrator" and e.body.startswith("pass integrator") for e in ev)
    integ.verify_integration().join(30)  # re-verifiable (task reopens), still no conflicts with the done state
    assert Tasks(board).get("integrator").status == "done"


def test_second_swarm_gets_its_own_integration_worktree_and_branch(repo):
    board, r, team, gate, integ = setup(repo, {"api": {"src/api/a.py": "A\n"}}, workers=("api",))
    gate.invoke("api", "go"); first = integ.merge_ready()
    wt1, b1 = integ.paths()
    board.start_swarm("second")
    r2 = Runner(board, repo, executor=Team(board, {"web": {"src/web/w.py": "W\n"}}))
    r2.define(WorkerSpec(name="web", goal="build web", owns=["src/web"]))
    gate2 = Gate(r2); gate2.invoke("web", "go"); integ2 = Integrator(gate2)
    out = integ2.merge_ready()
    wt2, b2 = integ2.paths()
    assert first["ok"] and out["ok"] and out["merged"] == ["web"]
    assert wt1 != wt2 and b1 != b2 and out["branch"] == b2
    assert (wt2 / "src/web/w.py").exists() and not (wt2 / "src/api/a.py").exists()  # nothing leaked from swarm 1
    assert not (wt1 / "src/web").exists()
