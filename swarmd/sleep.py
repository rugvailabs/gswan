"""Machine sleep detection, so a laptop that sleeps mid-run does not burn the wall-time budget or look like a hung worker.

`time.monotonic()` stops while the machine sleeps (macOS and Linux) and the wall clock does not, so comparing the two
between monitor ticks gives the time spent asleep. Sleeps are stored as wall-clock intervals and merged, so two
monitors watching the same swarm (MCP server + `swarm monitor --watch`) never count the same sleep twice.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from .board import Board

MIN_SLEEP_S = 20.0  # smaller gaps are scheduling jitter, not sleep

FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


class SleepClock:
    def __init__(self, wall=time.time, mono=time.monotonic):
        self.wall, self.mono = wall, mono
        self._last: tuple[float, float] | None = None

    def tick(self) -> tuple[datetime, datetime] | None:
        """The (start, end) of a sleep since the previous tick, or None."""
        w, m = self.wall(), self.mono()
        prev, self._last = self._last, (w, m)
        if prev is None:
            return None
        slept = (w - prev[0]) - (m - prev[1])
        if slept < MIN_SLEEP_S:
            return None
        end = datetime.fromtimestamp(w, timezone.utc)
        return end - timedelta(seconds=slept), end


def _merge(spans: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


class Sleeps:
    def __init__(self, board: Board):
        self.b = board

    def record(self, start: datetime, end: datetime) -> None:
        sw = self.b._require()
        with self.b._conn() as c:
            c.execute("INSERT INTO sleeps (swarm_id, start, end) VALUES (?,?,?)", (sw.id, start.strftime(FMT), end.strftime(FMT)))

    def spans(self) -> list[tuple[datetime, datetime]]:
        sw = self.b._require()
        with self.b._conn() as c:
            rows = c.execute("SELECT start, end FROM sleeps WHERE swarm_id=?", (sw.id,)).fetchall()
        parse = lambda s: datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)
        return _merge([(parse(r["start"]), parse(r["end"])) for r in rows])

    def slept_s(self, since: datetime, until: datetime) -> float:
        """Seconds asleep within [since, until]."""
        total = 0.0
        for s, e in self.spans():
            s, e = max(s, since), min(e, until)
            if e > s:
                total += (e - s).total_seconds()
        return total

    def last_wake(self) -> datetime | None:
        sp = self.spans()
        return sp[-1][1] if sp else None
