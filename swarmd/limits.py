"""Per-swarm budgets: workers, wake-ups (turns), wall time, cost. Exceeding one stops new turns and posts `blocked`.
Only a human raises limits (`swarm limits --set ...`); the coordinator can only choose them at swarm_start."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .board import Board

FIELDS = ("max_workers", "max_wakeups", "max_wall_s", "max_cost_usd")


@dataclass
class Limits:
    max_workers: int = 6
    max_wakeups: int = 40
    max_wall_s: int = 3600
    max_cost_usd: float = 10.0


class BudgetExceeded(Exception):
    """Raised when a swarm limit would be exceeded."""


class LimitsStore:
    def __init__(self, board: Board):
        self.b = board

    def get(self) -> Limits:
        sw = self.b._require()
        with self.b._conn() as c:
            r = c.execute("SELECT * FROM swarm_limits WHERE swarm_id=?", (sw.id,)).fetchone()
        return Limits(**{k: r[k] for k in FIELDS}) if r else Limits()

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
            cur[k] = type(getattr(Limits, k))(v)
        sw = self.b._require()
        with self.b._conn() as c:
            c.execute("INSERT INTO swarm_limits (swarm_id,max_workers,max_wakeups,max_wall_s,max_cost_usd) VALUES (?,?,?,?,?) "
                      "ON CONFLICT(swarm_id) DO UPDATE SET max_workers=excluded.max_workers, max_wakeups=excluded.max_wakeups, "
                      "max_wall_s=excluded.max_wall_s, max_cost_usd=excluded.max_cost_usd",
                      (sw.id, cur["max_workers"], cur["max_wakeups"], cur["max_wall_s"], cur["max_cost_usd"]))
        return Limits(**cur)

    def usage(self, now: datetime | None = None) -> dict:
        sw = self.b._require()
        now = now or datetime.now(timezone.utc)
        started = datetime.strptime(sw.created_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        with self.b._conn() as c:
            r = c.execute("SELECT COALESCE(SUM(CASE WHEN role='worker' THEN 1 ELSE 0 END),0) AS workers, "
                          "COALESCE(SUM(turns),0) AS wakeups, COALESCE(SUM(cost_usd),0) AS cost FROM workers WHERE swarm_id=?",
                          (sw.id,)).fetchone()
        return {"workers": r["workers"], "wakeups": r["wakeups"], "wall_s": int((now - started).total_seconds()),
                "cost_usd": round(r["cost"], 4)}

    def check_define(self, exempt: bool = False) -> None:
        lim, use = self.get(), self.usage()
        if not exempt and use["workers"] >= lim.max_workers:
            raise BudgetExceeded(f"worker limit reached ({use['workers']}/{lim.max_workers}); ask the user to raise it (`swarm limits --set max_workers=N`)")

    def check_turn(self, now: datetime | None = None) -> None:
        lim, use = self.get(), self.usage(now)
        for key, cap, label in (("wakeups", lim.max_wakeups, "wake-ups"), ("wall_s", lim.max_wall_s, "wall time (s)"), ("cost_usd", lim.max_cost_usd, "cost (USD)")):
            if use[key] >= cap:
                raise BudgetExceeded(f"swarm budget exhausted: {label} {use[key]}/{cap}. Stop and ask the user to raise it (`swarm limits --set ...`).")

    def snapshot(self) -> dict:
        return {"limits": asdict(self.get()), "usage": self.usage()}
