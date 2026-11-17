import asyncio
import io
import json

import pytest
from fastapi.testclient import TestClient

from swarmd import cli
from swarmd.api import create_app, event_stream
from swarmd.board import Board


@pytest.fixture
def board(tmp_path):
    return Board(tmp_path / "s.db")


def test_api_flow_camelcase_and_pagination(board):
    c = TestClient(create_app(board))
    assert c.post("/events", json={"agent": "a", "kind": "update", "body": "x"}).status_code == 409
    sw = c.post("/swarms", json={"task": "t"}).json()
    assert sw["id"].startswith("swm_") and "createdAt" in sw
    for i in range(3):
        r = c.post("/events", json={"agent": "api", "kind": "update", "body": f"m{i}", "taskId": "tsk_1"})
        assert r.status_code == 201 and r.json()["taskId"] == "tsk_1"
    page = c.get("/events", params={"limit": 2, "offset": 1}).json()
    assert page["total"] == 3 and [e["body"] for e in page["items"]] == ["m1", "m2"]
    assert c.get("/events", params={"kind": "bogus"}).status_code == 422
    assert c.get("/digest").json()["lastSeq"] == 3
    assert c.get("/stale-workers", params={"minutes": 10}).json() == []


def test_api_validation(board):
    c = TestClient(create_app(board)); c.post("/swarms", json={"task": "t"})
    assert c.post("/events", json={"agent": "bad name", "kind": "update", "body": "x"}).status_code == 422
    assert c.get("/events", params={"limit": 0}).status_code == 422


def test_api_token_auth(board):
    c = TestClient(create_app(board, token="s3cret"))
    assert c.get("/health").status_code == 200
    assert c.post("/swarms", json={"task": "t"}).status_code == 401
    h = {"Authorization": "Bearer s3cret"}
    assert c.post("/swarms", json={"task": "t"}, headers=h).status_code == 201
    assert c.get("/events?token=s3cret").status_code == 200
    assert c.get("/events?token=wrong").status_code == 401


def test_sse_stream_sees_events_from_other_writers(board):
    board.start_swarm("t")
    from swarmd.models import EventIn
    board.post(EventIn(agent="a", kind="claim", body="one"))

    async def run():
        out = []
        async def writer():
            await asyncio.sleep(0.15)
            board.post(EventIn(agent="b", kind="update", body="two"))
        w = asyncio.create_task(writer())
        async for item in event_stream(board, since=0, poll=0.05, max_idle=0.5):
            out.append(item)
        await w
        return out

    items = asyncio.run(run())
    assert [json.loads(i["data"])["body"] for i in items] == ["one", "two"]
    assert [i["id"] for i in items] == ["1", "2"]


def run_cli(tmp_path, monkeypatch, capsys, *argv):
    monkeypatch.setenv("SWARM_HOME", str(tmp_path))
    code = cli.main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    r = lambda *a: run_cli(tmp_path, monkeypatch, capsys, *a)
    assert r("post", "api", "claim", "x")[0] == 2  # no swarm yet
    code, out, _ = r("init", "build todo"); assert code == 0 and out.startswith("swm_")
    assert r("post", "api", "claim", "building api")[0] == 0
    assert r("post", "ui", "blocked", "need api")[0] == 0
    code, out, _ = r("show"); assert "building api" in out and "need api" in out
    code, out, _ = r("show", "1", "--json"); assert len(json.loads(out)) == 1
    code, out, _ = r("digest"); assert "BLOCKED" in out
    code, out, _ = r("stale", "0.00001", "--fail"); assert code == 1 and "STALE" in out
    code, out, _ = r("tail", "--once", "-n", "5"); assert "need api" in out
    assert "building api" in (tmp_path / ".swarm" / "board.md").read_text()
    assert r("post", "bad name", "update", "x")[0] == 2
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert r("post", "api", "update", "-")[0] == 2  # empty stdin is invalid
    monkeypatch.setattr("sys.stdin", io.StringIO("from stdin\n"))
    assert r("post", "api", "update", "-")[0] == 0
    assert "from stdin" in r("show")[1]


def test_status_and_mentions_endpoints(tmp_path):
    from swarmd.mcp_server import SwarmTools
    from swarmd.models import WorkerSpec
    from swarmd.runner import Runner
    from test_runner import FakeClaude, git
    root = tmp_path / "p"; root.mkdir()
    git(root, "init", "-q", "-b", "main"); git(root, "config", "user.email", "t@t"); git(root, "config", "user.name", "t")
    (root / "f").write_text("x"); git(root, "add", "."); git(root, "commit", "-qm", "i"); (root / ".swarm").mkdir()
    board = Board(root / ".swarm" / "swarm.db")
    runner = Runner(board, root, executor=FakeClaude())
    tools = SwarmTools(board, runner)
    c = TestClient(create_app(board, "tok", tools, ui_dir=None)); h = {"Authorization": "Bearer tok"}
    assert c.get("/status", headers=h).status_code == 409  # no swarm yet
    c.post("/swarms", json={"task": "t"}, headers=h)
    runner.define(WorkerSpec(name="api", goal="g", owns=["src"]))
    st = c.get("/status", headers=h).json()
    assert st["workers"][0]["name"] == "api" and st["workers"][0]["model"] == "sonnet" and st["allTasksSettled"] is False
    assert c.post("/workers/ghost/mentions", json={"message": "hi"}, headers=h).status_code == 404
    assert c.post("/workers/api/mentions", json={"message": ""}, headers=h).status_code == 422
    assert c.post("/workers/api/mentions", json={"message": "go"}, headers=h).status_code == 202
    assert c.post("/workers/api/mentions", json={"message": "go"}).status_code == 401
    out = tools.wait_for_workers(timeout_s=20)
    assert out["timedOut"] is False
    assert any(e["agent"] == "human" and e["kind"] == "mention" for e in c.get("/events", headers=h).json()["items"])


def test_static_ui_is_served_when_built(tmp_path, board):
    (tmp_path / "dist").mkdir(); (tmp_path / "dist" / "index.html").write_text("<h1>ui</h1>")
    c = TestClient(create_app(board, None, None, ui_dir=tmp_path / "dist"))
    assert "<h1>ui</h1>" in c.get("/").text and c.get("/health").json() == {"status": "ok"}


def test_find_root_prefers_swarm_dir_then_git_toplevel(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.delenv("SWARM_HOME", raising=False)
    proj = tmp_path / "proj"; (proj / "a" / "b").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    assert cli.find_root(proj / "a" / "b") == proj.resolve()  # git toplevel when no .swarm yet
    (proj / "a" / ".swarm").mkdir()
    assert cli.find_root(proj / "a" / "b") == (proj / "a").resolve()  # nearest .swarm wins
    plain = tmp_path / "plain"; plain.mkdir()
    assert cli.find_root(plain) == plain.resolve()  # neither -> cwd


def test_swarm_dir_ignores_itself(tmp_path):
    d = cli.ensure_swarm_dir(tmp_path)
    assert (d / ".gitignore").read_text() == "*\n"
    (d / ".gitignore").write_text("custom\n"); cli.ensure_swarm_dir(tmp_path)
    assert (d / ".gitignore").read_text() == "custom\n"  # never clobbered
