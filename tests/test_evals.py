import json
import shutil
import subprocess
from pathlib import Path

import pytest

from swarmd import evals
from swarmd.board import Board
from swarmd.cli import main
from swarmd.runner import Exec

ROOT = Path(evals.PROJECT_ROOT) / "evals"
TASKS = {t.id: t for t in evals.load_tasks(ROOT)}
OUT = json.dumps({"result": "done", "total_cost_usd": 0.5, "is_error": False})


def copy_reference(task, repo):
    ref = task.dir / "reference"
    shutil.copytree(ref, repo, dirs_exist_ok=True)


def test_tasks_are_discovered_and_well_formed():
    assert set(TASKS) >= {"textkit", "inventory-bugs"}
    for t in TASKS.values():
        assert t.prompt and (t.dir / "check").is_dir() and (t.dir / "reference").is_dir()


@pytest.mark.parametrize("tid", ["textkit", "inventory-bugs"])
def test_hidden_check_accepts_reference_and_rejects_the_starting_repo(tid, tmp_path):
    t = TASKS[tid]
    start = evals.prepare(t, tmp_path / "start")
    assert evals.check(t, start)[0] is False
    assert not (start / "_eval_check").exists()  # hidden tests never linger where an agent could read them
    ok = evals.prepare(t, tmp_path / "ok"); copy_reference(t, ok)
    assert evals.check(t, ok)[0] is True


def test_check_is_not_visible_in_the_agents_repo(tmp_path):
    repo = evals.prepare(TASKS["textkit"], tmp_path / "r")
    assert not list(repo.rglob("test_hidden.py"))


def test_single_mode_pass_and_fail(tmp_path):
    t = TASKS["inventory-bugs"]; calls = []

    def good(cmd, cwd, env, timeout):
        calls.append(cmd); copy_reference(t, cwd); return Exec(0, OUT, "")
    r = evals.run_one(t, "single", tmp_path / "a", executor=good, claude="claude")
    assert r.passed and r.cost_usd == 0.5 and r.escalations == 0
    cmd = calls[0]
    assert "--max-budget-usd" in cmd and "--plugin-dir" not in cmd and "Bash(python3:*)" in cmd

    bad = lambda cmd, cwd, env, timeout: Exec(0, OUT, "")  # noqa: E731
    r = evals.run_one(t, "single", tmp_path / "b", executor=bad)
    assert not r.passed


def fake_swarm(task, apply_reference=True, escalated=0):
    def run(cmd, cwd, env, timeout):
        board = Board(cwd / ".swarm" / "swarm.db"); sw = board.start_swarm("x")
        wt = cwd / ".swarm" / "wt" / f"_integration-{sw.id}"
        subprocess.run(["git", "-C", str(cwd), "worktree", "add", "-q", str(wt), "-b", f"swarm/{sw.id}/integration"], check=True, capture_output=True)
        if apply_reference:
            copy_reference(task, wt)
            env2 = dict(GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL="a@a", GIT_COMMITTER_NAME="a", GIT_COMMITTER_EMAIL="a@a", PATH=__import__("os").environ["PATH"])
            subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(wt), "commit", "-qm", "work"], check=True, capture_output=True, env=env2)
        with board._conn() as c:
            c.execute("INSERT INTO workers (id,swarm_id,name,goal,session_id,created_at,updated_at,cost_usd) VALUES ('w1',?,'a','g','s','t','t',1.25)", (sw.id,))
            c.execute("INSERT INTO tasks (id,swarm_id,worker,goal,status,created_at,updated_at) VALUES ('t1',?,'a','g',?, 't','t')", (sw.id, "escalated" if escalated else "done"))
        return Exec(0, OUT, "")
    return run


def test_swarm_mode_applies_integration_branch_and_sums_costs(tmp_path):
    t = TASKS["inventory-bugs"]
    r = evals.run_one(t, "swarm+all", tmp_path / "s", executor=fake_swarm(t), plugin_dir=Path("/plug"))
    assert r.passed and r.cost_usd == 1.75 and r.escalations == 0   # 0.5 coordinator + 1.25 workers


def test_swarm_mode_counts_escalations_and_flags_missing_integration(tmp_path):
    t = TASKS["inventory-bugs"]
    r = evals.run_one(t, "swarm", tmp_path / "e", executor=fake_swarm(t, apply_reference=False, escalated=1))
    assert not r.passed and r.escalations == 1


def test_swarm_command_by_mode(tmp_path):
    t = TASKS["textkit"]
    plain = evals.swarm_command(t, "swarm", "claude", "sonnet", 3, Path("/p"))[2]
    full = evals.swarm_command(t, "swarm+all", "claude", "sonnet", 3, Path("/p"))[2]
    assert plain.startswith("/todo:todo ") and "critics=true" not in plain and "define_researcher" not in plain
    assert "critics=true" in full and "define_researcher" in full and "Do NOT apply" in full
    assert "--plugin-dir" in evals.swarm_command(t, "swarm", "claude", "sonnet", 3, Path("/p"))


def test_report_summary_and_markdown():
    rep = evals.Report([evals.RunResult("t", "single", True, 0.4, 10), evals.RunResult("t", "single", False, 0.6, 20),
                        evals.RunResult("t", "swarm", True, 2.0, 60, escalations=1)])
    s = rep.summary()
    assert s["single"] == {"runs": 2, "passRate": 0.5, "meanCostUsd": 0.5, "meanWallS": 15.0, "escalations": 0, "blocked": 0}
    assert s["swarm"]["escalations"] == 1
    assert "| single | 2 | 50% | $0.500 |" in rep.markdown() and json.loads(rep.to_json())["runs"][2]["mode"] == "swarm"


def test_cli_eval_is_a_dry_run_without_yes(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_HOME", str(tmp_path)); monkeypatch.chdir(tmp_path)
    assert main(["eval", "--task", "textkit", "--mode", "single", "--mode", "swarm", "--repeat", "2"]) == 0
    out = capsys.readouterr().out
    assert "4 real run(s)" in out and "dry run: nothing executed" in out and not (ROOT / "results").exists()
    assert main(["eval", "--mode", "bogus"]) == 2
