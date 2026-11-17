import threading
from datetime import datetime, timedelta, timezone

import pytest

from swarmd.board import Board, NoActiveSwarm
from swarmd.models import EventIn


@pytest.fixture
def board(tmp_path):
    return Board(tmp_path / "s.db", markdown_path=tmp_path / "board.md")


def ev(agent="api", kind="update", body="hi", **kw):
    return EventIn(agent=agent, kind=kind, body=body, **kw)


def test_post_requires_active_swarm(board):
    with pytest.raises(NoActiveSwarm):
        board.post(ev())


def test_ids_are_prefixed_and_seq_increases(board):
    sw = board.start_swarm("t")
    assert sw.id.startswith("swm_")
    a, b = board.post(ev()), board.post(ev())
    assert a.id.startswith("evt_") and b.seq == a.seq + 1


def test_new_swarm_archives_old_and_isolates_events(board):
    board.start_swarm("one"); board.post(ev(body="old"))
    board.start_swarm("two"); board.post(ev(body="new"))
    page = board.list_events()
    assert [e.body for e in page.items] == ["new"]
    assert board.current_swarm().task == "two"


def test_pagination_and_filters(board):
    board.start_swarm("t")
    for i in range(7):
        board.post(ev(agent="a" if i % 2 else "b", kind="update", body=f"m{i}"))
    p = board.list_events(limit=3, offset=2)
    assert (p.total, len(p.items), p.limit, p.offset) == (7, 3, 3, 2)
    assert [e.body for e in p.items] == ["m2", "m3", "m4"]
    assert board.list_events(agent="a").total == 3
    assert board.list_events(since_seq=5).total == 2
    assert board.list_events(limit=9999).limit == 500


def test_redaction(board):
    board.start_swarm("t")
    e = board.post(ev(body="key sk-abcdefghijklmnopqrstuvwx and password=hunter2 ok"))
    assert "sk-abc" not in e.body and "hunter2" not in e.body and "ok" in e.body


def test_markdown_mirror_is_written(board, tmp_path):
    board.start_swarm("t"); board.post(ev(body="a | b"))
    md = (tmp_path / "board.md").read_text()
    assert "a ¦ b" in md and "| api | update |" in md


def test_stale_detects_silent_workers_only(board):
    board.start_swarm("t")
    board.post(ev("api", "claim", "x")); board.post(ev("ui", "claim", "y")); board.post(ev("ui", "done", "z"))
    board.post(ev("coordinator", "plan", "p"))
    later = datetime.now(timezone.utc) + timedelta(minutes=30)
    s = board.stale(10, now=later)
    assert [w.agent for w in s] == ["api"] and s[0].idle_minutes >= 29
    assert board.stale(10) == []


def test_digest_tracks_blockers_and_is_compact(board):
    board.start_swarm("build thing")
    board.post(ev("api", "claim", "start")); board.post(ev("api", "blocked", "need schema"))
    d = board.digest()
    assert d.open_blockers == ["api: need schema"] and "BLOCKED" in d.text
    board.post(ev("api", "update", "unblocked"))
    assert board.digest().open_blockers == []
    assert board.digest(since_seq=d.last_seq).recent[0].endswith("unblocked")


def test_concurrent_posts_are_all_stored(board):
    board.start_swarm("t")
    def work(n):
        for i in range(10):
            board.post(ev(f"w{n}", "update", f"m{i}"))
    ts = [threading.Thread(target=work, args=(n,)) for n in range(5)]
    [t.start() for t in ts]; [t.join() for t in ts]
    p = board.list_events(limit=500)
    assert p.total == 50 and len({e.seq for e in p.items}) == 50


def test_invalid_event_rejected():
    with pytest.raises(ValueError):
        EventIn(agent="bad agent!", kind="update", body="x")
    with pytest.raises(ValueError):
        EventIn(agent="a", kind="nope", body="x")
