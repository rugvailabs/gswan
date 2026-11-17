import json
import stat
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from swarmd.board import Board
from swarmd.gate import Gate
from swarmd.models import EventIn, WorkerSpec
from swarmd.monitor import Monitor, Thresholds
from swarmd.runner import Runner, StreamingExecutor, kill_tree
from swarmd.tasks import Tasks
from test_runner import FakeClaude, git, repo  # noqa: F401

TH = Thresholds(stall_s=300, loop_tools=8, loop_posts=3, idle_s=120, grace_s=60, max_nudges=2, max_restarts=1)
T0 = datetime(2026, 11, 17, 12, 0, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.fixture
def env(repo):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    fake = FakeClaude()
    r = Runner(board, repo, executor=fake)
    r.define(WorkerSpec(name="api", goal="g", owns=["src"]))
    gate = Gate(r); mon = Monitor(gate, TH)
    return board, r, fake, mon


def set_state(board, name, **cols):
    sets = ", ".join(f"{k}=?" for k in cols)
    with board._conn() as c:
        c.execute(f"UPDATE workers SET {sets} WHERE name=?", (*cols.values(), name))


def running(board, name, activity, **extra):
    set_state(board, name, status="running", pid=1, last_activity=iso(activity), activity_note="tool:Bash", **extra)


def states(mon, now):
    return {f.worker: (f.state, f.action) for f in mon.step(now)}


def test_healthy_waiting_and_idle(env):
    board, r, fake, mon = env
    running(board, "api", T0 - timedelta(seconds=10))
    assert states(mon, T0)["api"] == ("healthy", "none")
    set_state(board, "api", status="idle", pid=None)
    Tasks(board).request_verification("api")
    assert states(mon, T0)["api"] == ("waiting", "none")


def test_hung_ladder_nudge_restart_escalate(env):
    board, r, fake, mon = env
    running(board, "api", T0 - timedelta(seconds=900))
    assert states(mon, T0)["api"] == ("hung", "nudge")
    assert any(e.agent == "monitor" and e.kind == "nudge" for e in board.list_events().items)
    assert states(mon, T0 + timedelta(seconds=30))["api"] == ("hung", "none")  # inside grace period
    t1 = T0 + timedelta(seconds=90)  # past the grace period, still hung (a killed turn would unwind right after)
    out = {x.worker: x for x in mon.step(t1)}
    assert out["api"].action == "restart"
    set_state(board, "api", status="idle", pid=None)
    mon.join()
    assert any("terminated by the monitor" in c["cmd"][2] for c in fake.calls)
    assert any(e.agent == "monitor" and e.kind == "mention" for e in board.list_events().items)
    # still hung after the restart -> ladder exhausted -> escalate
    running(board, "api", T0 - timedelta(seconds=900))
    out = {x.worker: x for x in mon.step(t1 + timedelta(seconds=120))}
    assert out["api"].action == "escalate" and Tasks(board).get("api").status == "escalated"
    assert any(e.kind == "blocked" and "Needs a human" in e.body for e in board.list_events().items)


def test_looping_by_tool_repeat_and_by_board_posts(env):
    board, r, fake, mon = env
    running(board, "api", T0 - timedelta(seconds=5), tool_repeat=9)
    assert states(mon, T0)["api"][0] == "looping"
    set_state(board, "api", tool_repeat=0)
    for i in range(3):
        board.post(EventIn(agent="api", kind="update", body=f"retrying step {i}"))
    assert states(mon, T0 + timedelta(seconds=10))["api"][0] == "looping"  # digits normalized -> identical


def test_forgot_to_report_gets_reminders_then_escalates(env):
    board, r, fake, mon = env
    set_state(board, "api", status="idle", turns=1, last_activity=iso(T0 - timedelta(seconds=600)))
    assert states(mon, T0)["api"] == ("forgot", "remind"); mon.join()
    assert any("ended your turn without posting" in c["cmd"][2] for c in fake.calls)
    set_state(board, "api", status="idle", last_activity=iso(T0 - timedelta(seconds=600)))
    assert states(mon, T0 + timedelta(seconds=100))["api"] == ("forgot", "remind"); mon.join()
    set_state(board, "api", status="idle", last_activity=iso(T0 - timedelta(seconds=600)))
    assert states(mon, T0 + timedelta(seconds=200))["api"] == ("forgot", "escalate")
    assert Tasks(board).get("api").status == "escalated"


def test_reported_blocked_is_not_a_stall(env):
    board, r, fake, mon = env
    board.post(EventIn(agent="api", kind="blocked", body="need schema"))
    set_state(board, "api", status="idle", turns=1, last_activity=iso(T0 - timedelta(seconds=900)))
    assert states(mon, T0)["api"][0] == "blocked" and not fake.calls


def test_monitor_kill_is_reported_as_update_not_blocked(env):
    board, r, fake, mon = env
    fake.fail = True
    inner = r.exec
    r.exec = lambda *a: (r.reg.touch("api", "terminated by monitor"), inner(*a))[1]  # kill lands mid-turn
    r.invoke("api", "go")
    kinds = [(e.agent, e.kind) for e in board.list_events().items if e.agent == "swarmd"]
    assert ("swarmd", "update") in kinds and ("swarmd", "blocked") not in kinds


def test_progress_resets_ladder(env):
    board, r, fake, mon = env
    running(board, "api", T0 - timedelta(seconds=900))
    assert states(mon, T0)["api"][1] == "nudge" and r.reg.mon_get("api")["nudges"] == 1
    running(board, "api", T0 + timedelta(seconds=30))  # fresh output after the nudge
    assert states(mon, T0 + timedelta(seconds=40))["api"] == ("healthy", "none")
    assert r.reg.mon_get("api") == {"nudges": 0, "restarts": 0, "last_action": None, "judged": 0}


# ---- streaming executor (real subprocess, fake `claude`) ----------------------------------
FAKE_CLAUDE = r'''#!{py}
import json, sys, time
mode = sys.argv[1]
print(json.dumps({{"type": "system", "subtype": "init"}}), flush=True)
if mode == "loop":
    for _ in range(4):
        print(json.dumps({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "name": "Bash", "input": {{"command": "ls"}}}}]}}}}), flush=True)
        time.sleep(0.05)
if mode == "hang":
    time.sleep(60)
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": "fine", "total_cost_usd": 0.5, "permission_denials": []}}), flush=True)
'''


@pytest.fixture
def fake_cli(tmp_path):
    p = tmp_path / "fakeclaude"; p.write_text(FAKE_CLAUDE.format(py=sys.executable)); p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_streaming_executor_tracks_activity_and_loops(env, fake_cli, tmp_path):
    board, r, _, _ = env
    ex = StreamingExecutor(board)
    out = ex([fake_cli, "loop"], tmp_path, {"SWARM_WORKER": "api", "PATH": "/usr/bin:/bin"}, 30)
    assert out.returncode == 0 and json.loads(out.stdout)["result"] == "fine"
    w = r.reg.get("api")
    assert w.tool_repeat == 4 and w.activity_note == "tool:Bash" and w.last_activity and w.child_pid is None


def test_kill_tree_terminates_hung_turn(env, fake_cli, tmp_path):
    board, r, _, _ = env
    ex, res = StreamingExecutor(board), {}
    t = threading.Thread(target=lambda: res.update(out=ex([fake_cli, "hang"], tmp_path, {"SWARM_WORKER": "api", "PATH": "/usr/bin:/bin"}, 60)))
    t.start()
    for _ in range(50):
        pid = r.reg.get("api").child_pid
        if pid:
            break
        time.sleep(0.1)
    assert pid, "child pid should be recorded while the turn runs"
    kill_tree(pid); t.join(10)
    assert not t.is_alive() and res["out"].returncode < 0 and "terminated by signal" in res["out"].stderr
    assert r.reg.get("api").child_pid is None


def test_streaming_executor_timeout(env, fake_cli, tmp_path):
    board, r, _, _ = env
    out = StreamingExecutor(board)([fake_cli, "hang"], tmp_path, {"SWARM_WORKER": "api", "PATH": "/usr/bin:/bin"}, 1)
    assert out.returncode == 124 and "timed out" in out.stderr


# ---- LLM judge, tool history, silence data ---------------------------------------------------
from swarmd.monitor import build_judge_prompt, parse_judge_reply  # noqa: E402


def test_judge_extends_busy_worker_then_ladder_resumes(env):
    board, r, fake, mon = env
    calls = []
    mon.judge = lambda w, events, quiet: (calls.append(quiet) or (True, "pytest is still running"))
    running(board, "api", T0 - timedelta(seconds=900))
    assert states(mon, T0)["api"] == ("hung", "extend")
    assert any(e.agent == "monitor" and e.kind == "update" and "still progressing" in e.body for e in board.list_events().items)
    assert not any(e.kind == "nudge" for e in board.list_events().items)  # no nudge, no kill
    assert states(mon, T0 + timedelta(seconds=30))["api"] == ("hung", "none")  # grace
    assert states(mon, T0 + timedelta(seconds=90))["api"] == ("hung", "extend")  # second (last) judgement
    out = states(mon, T0 + timedelta(seconds=200))["api"]  # judgements exhausted: back to the normal ladder
    assert out == ("hung", "nudge") and len(calls) == 2


def test_judge_says_hung_or_fails_falls_through_to_nudge(env):
    board, r, fake, mon = env
    mon.judge = lambda w, e, q: (False, "sleeping")
    running(board, "api", T0 - timedelta(seconds=900))
    assert states(mon, T0)["api"] == ("hung", "nudge")


def test_judge_exception_free_none_falls_through(env):
    board, r, fake, mon = env
    mon.judge = lambda w, e, q: None  # e.g. claude unavailable
    running(board, "api", T0 - timedelta(seconds=900))
    assert states(mon, T0)["api"] == ("hung", "nudge")


def test_judge_not_used_for_loops(env):
    board, r, fake, mon = env
    mon.judge = lambda *a: (_ for _ in ()).throw(AssertionError("must not be called"))
    running(board, "api", T0 - timedelta(seconds=5), tool_repeat=9)
    assert states(mon, T0)["api"][0] == "looping"


def test_judge_prompt_and_reply_parsing(env):
    board, r, fake, mon = env
    set_state(board, "api", recent_tools='["Bash: pytest -x", "Read: a.py"]')
    p = build_judge_prompt(r.reg.get("api"), [], 700)
    assert "700 seconds" in p and "Bash: pytest -x" in p and '{"progress"' in p
    assert parse_judge_reply('sure: {"progress": true, "reason": "build"}') == (True, "build")
    assert parse_judge_reply('```json\n{"progress": false, "reason": "x"}\n```') == (False, "x")
    assert parse_judge_reply("no json here") is None and parse_judge_reply('{"progress": "maybe"') is None
    assert parse_judge_reply(None) is None


def test_streaming_executor_records_recent_tools_and_max_gap(env, fake_cli, tmp_path):
    board, r, _, _ = env
    StreamingExecutor(board)([fake_cli, "loop"], tmp_path, {"SWARM_WORKER": "api", "PATH": "/usr/bin:/bin"}, 30)
    w = r.reg.get("api")
    assert w.recent_tools and w.recent_tools[-1] == "Bash: ls" and len(w.recent_tools) <= 8
    assert w.max_gap_s >= 0.04  # the fake sleeps 0.05s between tool calls
