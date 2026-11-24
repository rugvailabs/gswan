"""Per-swarm budgets: workers, wake-ups (turns), wall time, tokens (input + output; cache reads are not counted). Exceeding one stops new turns and posts `blocked`.
Only a human raises limits (`swarm limits --set ...`); the coordinator can only choose them at swarm_start."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .board import Board

FIELDS = ("max_workers", "max_wakeups", "max_wall_s", "max_tokens")


@dataclass
class Limits:
    max_workers: int = 6
    max_wakeups: int = 40
    max_wall_s: int = 3600
    max_tokens: int = 3_000_000


class BudgetExceeded(Exception):
    """Raised when a swarm limit would be exceeded."""


class LimitsStore:
    def __init__(self, board: Board):
        self.b = board

    def get(self) -> Limits:
        sw = self.b._require()
        with self.b._conn() as c:
            r = c.execute("SELECT * FROM swarm_limits WHERE swarm_id=?", (sw.id,)).fetchone()
        lim = Limits(**{k: r[k] for k in FIELDS}) if r else Limits()
        ceiling = os.environ.get("SWARM_TOKEN_CEILING", "")  # set by the eval harness: a hard cap nobody inside the run can lift
        if ceiling.isdigit() and int(ceiling) > 0:
            lim.max_tokens = min(lim.max_tokens, int(ceiling))
        return lim

    def set(self, **kw) -> Limits:
        bad = [k for k in kw if k not in FIELDS]
        if bad:
            raise ValueError(f"unknown limit(s): {', '.join(bad)}; valid: {', '.join(FIELDS)}")
        cur = asdict(self.get())
        for k, v in kw.items():
            if v is None:
                continue
            if float(v) <= 0:
                raise ValueError(f"{k} must be positive")
            cur[k] = int(float(v))
        sw = self.b._require()
        with self.b._conn() as c:
            c.execute("INSERT INTO swarm_limits (swarm_id,max_workers,max_wakeups,max_wall_s,max_cost_usd,max_tokens) VALUES (?,?,?,?,0,?) "
                      "ON CONFLICT(swarm_id) DO UPDATE SET max_workers=excluded.max_workers, max_wakeups=excluded.max_wakeups, "
                      "max_wall_s=excluded.max_wall_s, max_tokens=excluded.max_tokens",
                      (sw.id, cur["max_workers"], cur["max_wakeups"], cur["max_wall_s"], cur["max_tokens"]))
        return Limits(**cur)

    def usage(self, now: datetime | None = None) -> dict:
        sw = self.b._require()
        now = now or datetime.now(timezone.utc)
        started = datetime.strptime(sw.created_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        with self.b._conn() as c:
            r = c.execute("SELECT COALESCE(SUM(CASE WHEN role='worker' THEN 1 ELSE 0 END),0) AS workers, "
                          "COALESCE(SUM(turns),0) AS wakeups, COALESCE(SUM(tokens_in+tokens_out+tokens_cache_write),0) AS tokens, "
                          "COALESCE(SUM(tokens_cache_read),0) AS cached FROM workers WHERE swarm_id=?",
                          (sw.id,)).fetchone()
        return {"workers": r["workers"], "wakeups": r["wakeups"], "wall_s": int((now - started).total_seconds()),
                "tokens": r["tokens"], "tokens_cached": r["cached"]}

    def check_define(self, exempt: bool = False) -> None:
        lim, use = self.get(), self.usage()
        if not exempt and use["workers"] >= lim.max_workers:
            raise BudgetExceeded(f"worker limit reached ({use['workers']}/{lim.max_workers}); ask the user to raise it (`swarm limits --set max_workers=N`)")

    def check_turn(self, now: datetime | None = None) -> None:
        lim, use = self.get(), self.usage(now)
        for key, cap, label in (("wakeups", lim.max_wakeups, "wake-ups"), ("wall_s", lim.max_wall_s, "wall time (s)"), ("tokens", lim.max_tokens, "tokens (input + output)")):
            if use[key] >= cap:
                raise BudgetExceeded(f"swarm budget exhausted: {label} {use[key]}/{cap}. Stop and ask the user to raise it (`swarm limits --set ...`).")

    def snapshot(self) -> dict:
        return {"limits": asdict(self.get()), "usage": self.usage()}
