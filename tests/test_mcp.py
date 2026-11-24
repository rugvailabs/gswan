import asyncio
import json
import sys
import threading
import time

import pytest

from swarmd.board import Board
from swarmd.mcp_server import SwarmTools, build_server
from swarmd.runner import Runner
from test_runner import FakeClaude, git, repo  # noqa: F401  (fixtures)


@pytest.fixture
def tools(repo):
    board = Board(repo / ".swarm" / "swarm.db")
    fake = FakeClaude(delay=0.3)
    return SwarmTools(board, Runner(board, repo, executor=fake)), fake


def test_requires_swarm_then_flow(tools):
    t, fake = tools
    assert t.define_worker("api", "g", ["src"])["ok"] is False  # no swarm yet
    assert t.swarm_start("task")["swarmId"].startswith("swm_")
    assert t.define_worker("api", "g", ["src/api"])["ok"]
    assert t.define_worker("api", "g", ["src/api"])["ok"] is False  # duplicate
    assert t.define_worker("Bad Name", "g", ["x"])["ok"] is False  # validation
    assert t.invoke_worker("ghost", "hi")["ok"] is False


def test_invoke_is_nonblocking_and_wait_returns_results(tools):
    t, fake = tools
    t.swarm_start("task"); t.define_worker("api", "g", ["src/api"]); t.define_worker("ui", "g", ["src/ui"])
    t0 = time.time()
    assert t.invoke_worker("api", "go")["started"] == "api" and t.invoke_worker("ui", "go")["started"] == "ui"
    assert time.time() - t0 < 0.2  # returned immediately
    out = t.wait_for_workers(timeout_s=20)
    assert out["timedOut"] is False and {w["name"] for w in out["workers"]} == {"api", "ui"}
    assert all(w["lastResult"]["ok"] and w["status"] == "idle" and not w["busy"] for w in out["workers"])
    assert any("mention" in e for e in out["newEvents"])
    assert fake.max_active == 2


def test_wait_times_out_while_busy(repo):
    board = Board(repo / ".swarm" / "swarm.db")
    t = SwarmTools(board, Runner(board, repo, executor=FakeClaude(delay=2.5)))
    t.swarm_start("x"); t.define_worker("api", "g", ["src"]); t.invoke_worker("api", "go")
    out = t.wait_for_workers(timeout_s=1)
    assert out["timedOut"] is True and out["workers"][0]["busy"] is True
    assert t.wait_for_workers(timeout_s=20)["timedOut"] is False


def test_board_tools_and_status(tools):
    t, _ = tools
    assert t.board_post("api", "update", "x")["ok"] is False  # no swarm
    t.swarm_start("task"); t.define_worker("api", "g", ["src"])
    assert t.board_post("api", "claim", "working")["seq"] >= 1
    assert t.board_post("api", "bogus", "x")["ok"] is False
    st = t.swarm_status()
    assert st["ok"] and st["workers"][0]["name"] == "api" and "working" in st["digest"]
    assert t.board_digest()["lastSeq"] >= 1


def test_role_filtering_and_real_stdio_server(tools, repo):
    t, _ = tools
    coord = {x.name for x in asyncio.run(build_server(t, "coordinator").list_tools())}
    worker = {x.name for x in asyncio.run(build_server(t, "worker").list_tools())}
    assert coord == {"swarm_start", "define_worker", "define_researcher", "critique_plan", "invoke_worker", "wait_for_workers", "swarm_status", "merge_workers", "record_decision", "board_post", "board_digest"}
    assert worker == {"board_post", "board_digest"}


def test_stdio_protocol_roundtrip(tmp_path):
    """Spawn the real server over stdio with the MCP client and call tools."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    (tmp_path / ".swarm").mkdir()

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "swarmd.mcp_server"],
                                       env={"SWARM_HOME": str(tmp_path), "PYTHONPATH": str(__import__("swarmd").__path__[0] + "/..")})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = {x.name for x in (await s.list_tools()).tools}
                started = await s.call_tool("swarm_start", {"task": "e2e"})
                post = await s.call_tool("board_post", {"agent": "api", "kind": "claim", "body": "hi"})
                return names, started, post
    names, started, post = asyncio.run(asyncio.wait_for(run(), 60))
    assert "invoke_worker" in names
    assert "swm_" in json.dumps(started.model_dump(), default=str) and "seq" in json.dumps(post.model_dump(), default=str)
