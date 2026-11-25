import json
from datetime import timedelta

from swarmd.board import Board
from swarmd.gate import Gate
from swarmd.limits import LimitsStore
from swarmd.models import WorkerSpec
from swarmd.monitor import Monitor
from swarmd.runner import Exec, Runner
from swarmd.sleep import SleepClock, Sleeps
from test_monitor import TH, T0, running, states
from test_runner import FakeClaude, git, repo  # noqa: F401


class Clocks:
    def __init__(self):
        self.w, self.m = 1_800_000_000.0, 100.0

    def advance(self, awake, asleep=0.0):
        self.w += awake + asleep
        self.m += awake


def test_clock_reports_only_real_sleep():
    c = Clocks(); clock = SleepClock(wall=lambda: c.w, mono=lambda: c.m)
    assert clock.tick() is None
    c.advance(15); assert clock.tick() is None
    c.advance(15, asleep=5); assert clock.tick() is None  # jitter
    c.advance(15, asleep=600)
    start, end = clock.tick()
    assert round((end - start).total_seconds()) == 600


def test_overlapping_sleeps_count_once_and_wall_budget_excludes_them(repo):
    board = Board(repo / ".swarm" / "swarm.db"); sw = board.start_swarm("t")
    lim = LimitsStore(board)
    from datetime import datetime, timezone
    started = datetime.strptime(sw.created_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    s = Sleeps(board)
    s.record(started + timedelta(seconds=100), started + timedelta(seconds=1100))
    s.record(started + timedelta(seconds=600), started + timedelta(seconds=1500))  # a second monitor saw the same sleep
    use = lim.usage(started + timedelta(seconds=2000))
    assert (use["slept_s"], use["wall_s"]) == (1400, 600)
    lim.check_turn(started + timedelta(seconds=4000))  # 4000s elapsed but only 2600s awake: under the 3600s budget


def test_worker_is_not_hung_right_after_a_wake(repo):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    r = Runner(board, repo, executor=FakeClaude()); r.define(WorkerSpec(name="api", goal="g", owns=["src"]))
    mon = Monitor(Gate(r), TH)
    running(board, "api", T0 - timedelta(seconds=2000))  # last output long ago...
    Sleeps(board).record(T0 - timedelta(seconds=1990), T0 - timedelta(seconds=60))  # ...because the machine slept
    assert states(mon, T0)["api"] == ("healthy", "none")
    assert states(mon, T0 + timedelta(seconds=400))["api"][0] == "hung"  # still quiet 300s+ after waking: a real stall


class SleepsOnce(FakeClaude):
    def __init__(self):
        super().__init__(); self.n = 0

    def __call__(self, cmd, cwd, env, timeout):
        self.n += 1
        if self.n == 1:
            self.calls.append({"cmd": cmd})
            return Exec(1, json.dumps({"is_error": True, "result": "API Error: Your computer went to sleep mid-response."}), "")
        return super().__call__(cmd, cwd, env, timeout)


def test_turn_cut_off_by_sleep_is_resumed(repo):
    board = Board(repo / ".swarm" / "swarm.db"); board.start_swarm("t")
    fake = SleepsOnce()
    r = Runner(board, repo, executor=fake); r.define(WorkerSpec(name="api", goal="g", owns=["src"]))
    res = r.invoke("api", "build it")
    assert res.ok and fake.n == 2
    assert "computer went to sleep" in fake.calls[-1]["cmd"][2]  # same session id (a created session falls back to --resume)
    assert fake.calls[0]["cmd"][fake.calls[0]["cmd"].index("--session-id") + 1] in fake.calls[-1]["cmd"]
    assert any("cut off because the computer slept" in e.body for e in board.list_events().items)
    assert not any(e.kind == "blocked" for e in board.list_events().items)
