"""SQLite-backed append-only board. One short-lived connection per call (WAL), safe across processes."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .models import (
    SYSTEM_AGENTS, Digest, Event, EventIn, EventPage, StaleWorker, Swarm, new_id, utc_now,
)
from .redact import redact

SCHEMA = """
CREATE TABLE IF NOT EXISTS swarms (
  id TEXT PRIMARY KEY, task TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
  swarm_id TEXT NOT NULL REFERENCES swarms(id), ts TEXT NOT NULL,
  agent TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
  task_id TEXT, refs TEXT NOT NULL DEFAULT '[]');
CREATE INDEX IF NOT EXISTS idx_events_swarm ON events(swarm_id, seq);
CREATE TABLE IF NOT EXISTS workers (
  id TEXT PRIMARY KEY, swarm_id TEXT NOT NULL REFERENCES swarms(id), name TEXT NOT NULL,
  goal TEXT NOT NULL, owns TEXT NOT NULL DEFAULT '[]', tools TEXT NOT NULL DEFAULT '[]',
  model TEXT NOT NULL DEFAULT 'sonnet', session_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'idle',
  worktree TEXT, branch TEXT, turns INTEGER NOT NULL DEFAULT 0, cost_usd REAL NOT NULL DEFAULT 0,
  tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0,
  tokens_cache_write INTEGER NOT NULL DEFAULT 0, tokens_cache_read INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, pid INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE (swarm_id, name));
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, swarm_id TEXT NOT NULL REFERENCES swarms(id), worker TEXT NOT NULL, goal TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', rounds INTEGER NOT NULL DEFAULT 0, reasons TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (swarm_id, worker));
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, swarm_id TEXT NOT NULL, worker TEXT NOT NULL, command TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  program TEXT, status TEXT NOT NULL DEFAULT 'pending', decision TEXT, note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, decided_at TEXT);
CREATE INDEX IF NOT EXISTS idx_approvals ON approvals(swarm_id, worker, status);
CREATE TABLE IF NOT EXISTS paused (
  swarm_id TEXT NOT NULL, worker TEXT NOT NULL, reason TEXT NOT NULL, ts TEXT NOT NULL, PRIMARY KEY (swarm_id, worker));
CREATE TABLE IF NOT EXISTS ran_commands (
  swarm_id TEXT NOT NULL, worker TEXT NOT NULL, ts TEXT NOT NULL, command TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_ran ON ran_commands(swarm_id, worker);
CREATE TABLE IF NOT EXISTS swarm_limits (
  swarm_id TEXT PRIMARY KEY, max_workers INTEGER NOT NULL, max_wakeups INTEGER NOT NULL,
  max_wall_s INTEGER NOT NULL, max_cost_usd REAL NOT NULL DEFAULT 0, max_tokens INTEGER NOT NULL DEFAULT 3000000);
CREATE TABLE IF NOT EXISTS monitor_state (
  swarm_id TEXT NOT NULL, worker TEXT NOT NULL, nudges INTEGER NOT NULL DEFAULT 0, restarts INTEGER NOT NULL DEFAULT 0,
  last_action TEXT, PRIMARY KEY (swarm_id, worker));
CREATE TABLE IF NOT EXISTS critiques (
  id INTEGER PRIMARY KEY AUTOINCREMENT, swarm_id TEXT NOT NULL, target TEXT NOT NULL, critic TEXT NOT NULL,
  round INTEGER NOT NULL, severity TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS critic_rounds (
  swarm_id TEXT NOT NULL, target TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (swarm_id, target));
CREATE TABLE IF NOT EXISTS worker_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT, worker_id TEXT NOT NULL REFERENCES workers(id),
  sender TEXT NOT NULL, message TEXT NOT NULL, ts TEXT NOT NULL);
"""


class NoActiveSwarm(Exception):
    """Raised when posting/reading without an active swarm."""


class Board:
    def __init__(self, db_path: str | Path, markdown_path: str | Path | None = None):
        self.db_path = Path(db_path)
        self.markdown_path = Path(markdown_path) if markdown_path else None
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            have = {r["name"] for r in c.execute("PRAGMA table_info(workers)")}
            for col, ddl in (("role", "TEXT NOT NULL DEFAULT 'worker'"), ("base_sha", "TEXT"), ("cwd", "TEXT"),
                             ("last_activity", "TEXT"), ("activity_note", "TEXT"), ("tool_sig", "TEXT"),
                             ("tool_repeat", "INTEGER NOT NULL DEFAULT 0"), ("child_pid", "INTEGER"),
                             ("max_gap_s", "REAL NOT NULL DEFAULT 0"), ("recent_tools", "TEXT NOT NULL DEFAULT '[]'"),
                             ("tokens_in", "INTEGER NOT NULL DEFAULT 0"), ("tokens_out", "INTEGER NOT NULL DEFAULT 0"),
                             ("tokens_cache_write", "INTEGER NOT NULL DEFAULT 0"), ("tokens_cache_read", "INTEGER NOT NULL DEFAULT 0"),
                             ("checks", "TEXT NOT NULL DEFAULT '[]'"), ("grp", "TEXT")):
                if col not in have:
                    c.execute(f"ALTER TABLE workers ADD COLUMN {col} {ddl}")
            if "max_tokens" not in {r["name"] for r in c.execute("PRAGMA table_info(swarm_limits)")}:  # budgets were in USD before
                c.execute("ALTER TABLE swarm_limits ADD COLUMN max_tokens INTEGER NOT NULL DEFAULT 3000000")
            if "judged" not in {r["name"] for r in c.execute("PRAGMA table_info(monitor_state)")}:
                c.execute("ALTER TABLE monitor_state ADD COLUMN judged INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        try:
            with c:  # commit/rollback
                yield c
        finally:
            c.close()

    # ---- swarms ---------------------------------------------------------
    def start_swarm(self, task: str, limits: dict | None = None) -> Swarm:
        from .limits import Limits  # local import: limits depends on Board
        sw = Swarm(id=new_id("swm"), task=task, status="active", created_at=utc_now())
        lim = Limits(**{k: v for k, v in (limits or {}).items() if v is not None})
        with self._conn() as c:
            c.execute("UPDATE swarms SET status='archived' WHERE status='active'")
            c.execute("INSERT INTO swarms VALUES (?,?,?,?)", (sw.id, sw.task, sw.status, sw.created_at))
            c.execute("INSERT INTO swarm_limits (swarm_id,max_workers,max_wakeups,max_wall_s,max_cost_usd,max_tokens) VALUES (?,?,?,?,0,?)",
                      (sw.id, lim.max_workers, lim.max_wakeups, lim.max_wall_s, lim.max_tokens))
        self._export()
        return sw

    def post_once(self, ev: EventIn) -> Event | None:
        """Post unless the same agent's latest event is identical (avoids spamming repeated warnings)."""
        sw = self._require()
        with self._conn() as c:
            r = c.execute("SELECT kind, body FROM events WHERE swarm_id=? AND agent=? ORDER BY seq DESC LIMIT 1", (sw.id, ev.agent)).fetchone()
        if r and r["kind"] == ev.kind and r["body"] == redact(ev.body).replace("\r", ""):
            return None
        return self.post(ev)

    def current_swarm(self) -> Swarm | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM swarms WHERE status='active' ORDER BY created_at DESC LIMIT 1").fetchone()
        return Swarm(**dict(r)) if r else None

    def _require(self) -> Swarm:
        sw = self.current_swarm()
        if sw is None:
            raise NoActiveSwarm("no active swarm; run `swarm init \"<task>\"` first")
        return sw

    # ---- events ---------------------------------------------------------
    def post(self, ev: EventIn) -> Event:
        sw = self._require()
        eid, ts = new_id("evt"), utc_now()
        body = redact(ev.body).replace("\r", "")
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO events (id,swarm_id,ts,agent,kind,body,task_id,refs) VALUES (?,?,?,?,?,?,?,?)",
                (eid, sw.id, ts, ev.agent, ev.kind, body, ev.task_id, json.dumps(ev.refs)),
            )
            seq = cur.lastrowid
            if ev.kind == "done":  # a worker's `done` is provisional until the verifier passes it
                c.execute("UPDATE tasks SET status='awaiting_verification', updated_at=? "
                          "WHERE swarm_id=? AND worker=? AND status IN ('open','failed')", (ts, sw.id, ev.agent))
        out = Event(seq=seq, id=eid, swarm_id=sw.id, ts=ts, agent=ev.agent, kind=ev.kind,
                    body=body, task_id=ev.task_id, refs=ev.refs)
        self._export()
        return out

    @staticmethod
    def _row(r: sqlite3.Row) -> Event:
        d = dict(r)
        d["refs"] = json.loads(d["refs"])
        return Event(**d)

    def list_events(self, *, limit: int = 50, offset: int = 0, since_seq: int = 0,
                    kind: str | None = None, agent: str | None = None,
                    swarm_id: str | None = None) -> EventPage:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        sid = swarm_id or self._require().id
        where, args = ["swarm_id=?", "seq>?"], [sid, since_seq]
        if kind:
            where.append("kind=?"); args.append(kind)
        if agent:
            where.append("agent=?"); args.append(agent)
        w = " AND ".join(where)
        with self._conn() as c:
            total = c.execute(f"SELECT COUNT(*) FROM events WHERE {w}", args).fetchone()[0]
            rows = c.execute(f"SELECT * FROM events WHERE {w} ORDER BY seq LIMIT ? OFFSET ?",
                             [*args, limit, offset]).fetchall()
        return EventPage(items=[self._row(r) for r in rows], total=total, limit=limit, offset=offset)

    def events_after(self, seq: int, limit: int = 200) -> list[Event]:
        """Events newer than `seq` in the active swarm (used by SSE/tail). Empty if no swarm."""
        sw = self.current_swarm()
        if sw is None:
            return []
        with self._conn() as c:
            rows = c.execute("SELECT * FROM events WHERE swarm_id=? AND seq>? ORDER BY seq LIMIT ?",
                             (sw.id, seq, limit)).fetchall()
        return [self._row(r) for r in rows]

    def last_seq(self) -> int:
        sw = self.current_swarm()
        if sw is None:
            return 0
        with self._conn() as c:
            return c.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE swarm_id=?", (sw.id,)).fetchone()[0]

    # ---- derived views --------------------------------------------------
    def _all(self, sw: Swarm) -> list[Event]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM events WHERE swarm_id=? ORDER BY seq", (sw.id,)).fetchall()
        return [self._row(r) for r in rows]

    def stale(self, minutes: float = 10, now: datetime | None = None) -> list[StaleWorker]:
        """Workers (non-system agents) whose latest event is not `done` and is older than `minutes`."""
        sw = self._require()
        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        latest: dict[str, Event] = {}
        for e in self._all(sw):
            if e.agent not in SYSTEM_AGENTS and not e.agent.startswith("verify-"):
                latest[e.agent] = e
        out = []
        for agent, e in latest.items():
            if e.kind != "done" and e.ts < cutoff:
                last = datetime.strptime(e.ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
                out.append(StaleWorker(agent=agent, last_ts=e.ts, last_kind=e.kind,
                                       idle_minutes=round((now - last).total_seconds() / 60, 1)))
        return sorted(out, key=lambda s: s.agent)

    def digest(self, since_seq: int = 0, recent: int = 8) -> Digest:
        """Compact view for agents: avoids re-reading full history (cost control, design §3.2)."""
        sw = self._require()
        evs = self._all(sw)
        counts: dict[str, int] = {}
        agents: dict[str, str] = {}
        blockers: dict[str, str] = {}
        for e in evs:
            counts[e.kind] = counts.get(e.kind, 0) + 1
            agents[e.agent] = f"{e.kind}: {e.body[:160]}"
            if e.kind == "blocked":
                blockers[e.agent] = e.body[:160]
            elif e.agent in blockers and e.kind in ("update", "done", "claim"):
                blockers.pop(e.agent)
        new = [e for e in evs if e.seq > since_seq]
        rec = [f"#{e.seq} {e.agent}/{e.kind}: {e.body[:120]}" for e in new[-recent:]]
        lines = [f"Swarm {sw.id}: {sw.task}", f"events={len(evs)} " + " ".join(f"{k}={v}" for k, v in sorted(counts.items()))]
        lines += [f"- {a}: {s}" for a, s in sorted(agents.items())]
        if blockers:
            lines += ["BLOCKED:"] + [f"- {a}: {b}" for a, b in sorted(blockers.items())]
        if rec:
            lines += ["Recent:"] + rec
        return Digest(swarm_id=sw.id, task=sw.task, last_seq=evs[-1].seq if evs else 0, counts=counts,
                      agents=agents, open_blockers=[f"{a}: {b}" for a, b in sorted(blockers.items())],
                      recent=rec, text="\n".join(lines))

    # ---- markdown mirror (for agents without MCP, and for git) ------------
    def markdown(self) -> str:
        sw = self.current_swarm()
        if sw is None:
            return "# Swarm board\n\n_No active swarm._\n"
        rows = ["| seq | time | agent | kind | message |", "|---|---|---|---|---|"]
        for e in self._all(sw):
            msg = e.body.replace("\n", " ").replace("|", "¦")
            rows.append(f"| {e.seq} | {e.ts[:19].replace('T', ' ')} | {e.agent} | {e.kind} | {msg} |")
        return f"# Swarm board\n\n**Swarm:** {sw.id}\n**Task:** {sw.task}\n**Started:** {sw.created_at}\n\n" + "\n".join(rows) + "\n"

    def _export(self) -> None:
        if self.markdown_path is None:
            return
        try:
            tmp = self.markdown_path.with_suffix(".tmp")
            tmp.write_text(self.markdown())
            tmp.replace(self.markdown_path)
        except OSError:
            pass  # mirror is best-effort; the DB is the source of truth
