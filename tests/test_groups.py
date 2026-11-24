"""Step 3: groups with @group / @all mentions, and moving an idle worker to another model."""
import json

import pytest
from fastapi.testclient import TestClient

from swarmd.api import create_app
from swarmd.board import Board
from swarmd.cli import main
from swarmd.gate import Gate
from swarmd.mcp_server import SwarmTools
from swarmd.models import EventIn, WorkerSpec
from swarmd.runner import Exec, Runner
from swarmd.workers import WorkerError
from test_runner import git, repo  # noqa: F401


class Script:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, cwd, env, timeout):
        self.calls.append((env["SWARM_WORKER"], cmd[2], cmd[cmd.index("--model") + 1]))
        return Exec(0, json.dumps({"result": "ok", "is_error": False, "permission_denials": []}), "")


def setup(repo):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    ex = Script()
    r = Runner(board, repo, executor=ex)
    r.define(WorkerSpec(name="api", goal="g", owns=["src/api"], group="backend"))
    r.define(WorkerSpec(name="db", goal="g", owns=["src/db"], group="backend"))
    r.define(WorkerSpec(name="ui", goal="g", owns=["src/ui"], group="frontend"))
    return board, r, ex, SwarmTools(board, r, Gate(r))


def wait(tools):
    for t in list(tools.threads.values()):
        t.join(5)


def test_resolve_names_groups_and_all(repo):
    board, r, _, _ = setup(repo)
    assert [w.name for w in r.reg.resolve("backend")] == ["api", "db"]
    assert [w.name for w in r.reg.resolve("ui")] == ["ui"]
    assert [w.name for w in r.reg.resolve("all")] == ["api", "db", "ui"]
    assert r.reg.groups() == {"backend": ["api", "db"], "frontend": ["ui"]}
    with pytest.raises(WorkerError, match="groups: backend, frontend"):
        r.reg.resolve("nope")


@pytest.mark.parametrize("spec,why", [
    (dict(name="backend", goal="g", owns=["x"]), "already a group name"),
    (dict(name="x", goal="g", owns=["x"], group="api"), "same name as a worker"),
    (dict(name="all", goal="g", owns=["x"]), "reserved"),
    (dict(name="x", goal="g", owns=["x"], group="all"), "reserved"),
])
def test_names_and_groups_cannot_collide(repo, spec, why):
    board, r, _, _ = setup(repo)
    with pytest.raises(WorkerError, match=why):
        r.define(WorkerSpec(**spec))


def test_coordinator_messages_a_group_with_one_board_event(repo):
    board, r, ex, tools = setup(repo)
    out = tools.invoke_worker("backend", "use the new schema")
    wait(tools)
    assert out["ok"] and sorted(out["started"]) == ["api", "db"] and out["group"] == "backend"
    assert sorted(w for w, p, _ in ex.calls) == ["api", "db"] and all(p == "use the new schema" for _, p, _ in ex.calls)
    ms = [e for e in board.list_events().items if e.kind == "mention"]
    assert len(ms) == 1 and ms[0].body == "@backend use the new schema" and sorted(ms[0].refs) == ["to:api", "to:db"]
    tools.invoke_worker("all", "stop and commit"); wait(tools)
    assert sorted(w for w, p, _ in ex.calls if p == "stop and commit") == ["api", "db", "ui"]
    assert tools.invoke_worker("nobody", "x")["ok"] is False


def test_single_worker_mentions_are_tagged_too(repo):
    board, r, ex, tools = setup(repo)
    tools.invoke_worker("ui", "hi"); wait(tools)
    m = [e for e in board.list_events().items if e.kind == "mention"][-1]
    assert m.body == "@ui hi" and m.refs == ["to:ui"]


def test_worker_group_mention_skips_itself_and_counts_toward_the_pair_cap(repo):
    board, r, ex, tools = setup(repo)
    out = r.enqueue_mention("api", "backend", "schema changed")
    assert out["ok"] and out["queued"] == "db" and r.reg.queue_len(r.reg.get("api")) == 0
    assert r.enqueue_mention("api", "all", "heads up")["queued"] == "db, ui"
    for _ in range(r.MAX_PEER_MESSAGES - 2):
        r.enqueue_mention("api", "db", "more")
    out = r.enqueue_mention("api", "all", "again")
    assert out["ok"] and out["queued"] == "ui" and out["skipped"] == ["db"]  # db hit its cap through group + direct messages
    assert r.enqueue_mention("api", "api", "me")["ok"] is False


def test_human_cli_mentions_a_group(repo, monkeypatch, capsys):
    board, r, _, _ = setup(repo)
    monkeypatch.setenv("SWARM_HOME", str(repo)); monkeypatch.delenv("SWARM_ROLE", raising=False)
    assert main(["mention", "backend", "please rebase"]) == 0
    assert "api, db" in capsys.readouterr().out
    assert r.reg.queue_len(r.reg.get("api")) == 1 and r.reg.queue_len(r.reg.get("ui")) == 0
    assert main(["mention", "ghost", "x"]) == 2


def test_charter_lists_groups(repo):
    board, r, _, _ = setup(repo)
    text = r.teammates(r.reg.get("api"))
    assert "Groups: backend = api, db; frontend = ui" in text and "db [backend]" in text


# ---- model switch ------------------------------------------------------------------------------------------------

def test_coordinator_switches_an_idle_workers_model_with_a_reason(repo):
    board, r, ex, tools = setup(repo)
    assert tools.set_worker_model("api", "haiku", "short")["ok"] is False  # a reason is required
    assert r.reg.get("api").model == "sonnet"
    out = tools.set_worker_model("api", "haiku", "the parser is simple; haiku is enough")
    assert out == {"ok": True, "worker": "api", "from": "sonnet", "to": "haiku"}
    d = [e for e in board.list_events().items if e.kind == "decision"][-1]
    assert d.agent == "coordinator" and d.body.startswith("[other] api model sonnet -> haiku: the parser is simple")
    tools.invoke_worker("api", "continue"); wait(tools)
    assert ex.calls[-1] == ("api", "continue", "haiku")  # next turn uses the new model on the same session
    assert tools.set_worker_model("api", "gpt-5", "x" * 30)["ok"] is False


def test_model_cannot_change_mid_turn_or_for_other_roles(repo):
    import os
    board, r, _, tools = setup(repo)
    w = r.reg.get("api")
    with board._conn() as c:
        c.execute("UPDATE workers SET status='running', pid=? WHERE id=?", (os.getpid(), w.id))
    assert "is running a turn" in tools.set_worker_model("api", "haiku", "y" * 30)["error"]
    r.reg.define_verifier(r.reg.get("db"))
    assert "only workers" in tools.set_worker_model("verify-db", "haiku", "y" * 30)["error"]


def test_human_switches_model_from_api_and_cli(repo, monkeypatch, capsys):
    board, r, _, tools = setup(repo)
    c = TestClient(create_app(board, "tok", tools, ui_dir=None)); h = {"Authorization": "Bearer tok"}
    assert c.post("/workers/ui/model", json={"model": "opus"}, headers=h).json()["to"] == "opus"
    assert c.post("/workers/ui/model", json={"model": "llama"}, headers=h).status_code == 422
    assert [e for e in board.list_events().items if e.kind == "decision"][-1].agent == "human"
    monkeypatch.setenv("SWARM_HOME", str(repo))
    monkeypatch.setenv("SWARM_ROLE", "worker")
    assert main(["worker", "model", "ui", "haiku"]) == 3
    monkeypatch.delenv("SWARM_ROLE")
    assert main(["worker", "model", "ui", "haiku", "--why", "cheaper for docs"]) == 0
    assert r.reg.get("ui").model == "haiku" and "opus -> haiku" in capsys.readouterr().out


def test_status_shows_groups(repo):
    board, r, _, tools = setup(repo)
    ws = {w["name"]: w for w in tools.swarm_status()["workers"]}
    assert ws["api"]["group"] == "backend" and ws["ui"]["model"] == "sonnet"
