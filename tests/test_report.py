import json
import subprocess

from swarmd import report
from swarmd.board import Board
from swarmd.cli import main
from swarmd.critics import Critiques
from swarmd.models import EventIn, WorkerSpec
from swarmd.runner import Exec, Runner
from swarmd.tasks import Tasks
from test_runner import git, repo  # noqa: F401


class Fake:
    def __call__(self, cmd, cwd, env, timeout):
        return Exec(0, json.dumps({"result": "did it", "total_cost_usd": 0.02, "usage": {"input_tokens": 100, "output_tokens": 50, "cache_creation_input_tokens": 900, "cache_read_input_tokens": 4000}, "is_error": False, "permission_denials": []}), "")


def make(repo):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("ship feature")
    r = Runner(board, repo, executor=Fake()); r.define(WorkerSpec(name="api", goal="build api", owns=["src/api"]))
    r.invoke("api", "go")  # creates the worktree + branch
    w = r.reg.get("api")
    return board, r, w


def commit(w, files):
    for f in files:
        p = __import__("pathlib").Path(w.worktree) / f
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text("x")
    subprocess.run(["git", "-C", w.worktree, "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", w.worktree, "-c", "user.email=a@a", "-c", "user.name=a", "commit", "-qm", "w"], check=True, capture_output=True)


def test_scope_drift_is_detected(repo):
    board, r, w = make(repo)
    commit(w, ["src/api/a.py", "src/api/sub/b.py", "docs/oops.md"])
    d = report.build(board, repo)
    api = d["workers"][0]
    assert sorted(api["filesChanged"]) == ["docs/oops.md", "src/api/a.py", "src/api/sub/b.py"]
    assert api["outOfScope"] == ["docs/oops.md"]
    md = report.markdown(d)
    assert "Scope drift" in md and "docs/oops.md" in md and "| api |" in md


def test_in_scope_matching():
    assert report.in_scope("src/api/a.py", ["src/api"]) and report.in_scope("src/api", ["src/api/"])
    assert not report.in_scope("src/apix/a.py", ["src/api"])
    assert report.in_scope("anything/x.py", ["."])  # the integrator owns the whole integration worktree


def test_report_collects_sections_and_tokens_by_role(repo):
    board, r, w = make(repo)
    r.define_researcher("docs", "q"); board.post(EventIn(agent="docs", kind="research", body='c | https://x.dev | "q"'))
    Critiques(board).begin_round("api"); Critiques(board).add("api", "critic-api-security", "blocker", "SQL injection", "a.py:3")
    board.post(EventIn(agent="coordinator", kind="decision", body="split api/ui because they share no files"))
    board.post(EventIn(agent="monitor", kind="nudge", body="api hung: quiet 300s [rule: stall_s=300]"))
    Tasks(board).submit_verdict("api", True, ["ok"]) if Tasks(board).get("api").status in ("verifying", "awaiting_verification") else None
    d = report.build(board, repo)
    assert d["agentsByRole"] == {"worker": 1, "researcher": 1} and d["tokensByRole"]["worker"] == {"input": 1000, "output": 50, "cached": 4000, "total": 1050}
    assert d["workers"][0]["tokens"]["total"] == 1050 and "costUsd" not in d["workers"][0] and d["usage"]["tokens"] == 1050
    assert d["workers"][0]["critiques"] == {"blocker": 1, "major": 0, "nit": 0}
    assert any("split api/ui" in x for x in d["decisions"]) and any("https://x.dev" in x for x in d["research"])
    assert any("SQL injection" in x for x in d["critiques"]) and any("rule: stall_s" in x for x in d["monitor"])
    md = report.markdown(d)
    assert "tokens 1.1k/3M (input + output) · 4k more read from cache" in md and "worker 1k in / 50 out (+4k cached) (1 agents)" in md
    assert "| 1k / 50 |" in md and "$" not in md
    for h in ("## Budget", "## Plan vs actual", "## Decisions", "## Research findings", "## Critic findings", "## Monitor actions", "## Denied tool calls"):
        assert h in md


def test_reply_events_carry_provenance(repo):
    board, r, w = make(repo)
    reply = [e for e in board.list_events().items if "replied" in e.body][0]
    assert "reply:api" in reply.refs and "tokens:1000:50:4000" in reply.refs and "turn:1" in reply.refs and any(x.startswith("session:") for x in reply.refs)


def test_cli_and_api_report(repo, monkeypatch, capsys):
    from fastapi.testclient import TestClient
    from swarmd.api import create_app
    board, r, w = make(repo)
    monkeypatch.setenv("SWARM_HOME", str(repo)); monkeypatch.chdir(repo)
    assert main(["report"]) == 0 and "# Run report — ship feature" in capsys.readouterr().out
    out = repo / "r.md"
    assert main(["report", "--out", str(out)]) == 0 and "Plan vs actual" in out.read_text()
    c = TestClient(create_app(board, None))
    assert c.get("/report").json()["workers"][0]["name"] == "api"
    assert "## Budget" in c.get("/report?format=markdown").json()["markdown"]
