"""Pydantic models shared by the board service, API and CLI. JSON uses camelCase."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

EventKind = Literal[
    "plan", "claim", "update", "blocked", "done", "verify", "nudge", "mention", "human", "research", "critique", "decision", "approval"
]
EVENT_KINDS: tuple[str, ...] = EventKind.__args__  # type: ignore[attr-defined]
SYSTEM_AGENTS = frozenset({"coordinator", "monitor", "verifier", "human", "swarmd"})


def new_id(prefix: str) -> str:
    """Prefixed entity id, e.g. swm_3fa91c2b0d4e."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Swarm(CamelModel):
    id: str
    task: str
    status: Literal["active", "archived"]
    created_at: str


class EventIn(CamelModel):
    agent: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    kind: EventKind
    body: str = Field(min_length=1, max_length=8000)
    task_id: str | None = Field(default=None, max_length=64)
    refs: list[str] = Field(default_factory=list, max_length=32)


class Event(CamelModel):
    seq: int
    id: str
    swarm_id: str
    ts: str
    agent: str
    kind: EventKind
    body: str
    task_id: str | None = None
    refs: list[str] = Field(default_factory=list)


class EventPage(CamelModel):
    items: list[Event]
    total: int
    limit: int
    offset: int


class SwarmCreate(CamelModel):
    task: str = Field(min_length=1, max_length=2000)


class StaleWorker(CamelModel):
    agent: str
    last_ts: str
    last_kind: EventKind
    idle_minutes: float


class Digest(CamelModel):
    swarm_id: str
    task: str
    last_seq: int
    counts: dict[str, int]
    agents: dict[str, str]  # agent -> "<kind>: <body>" of its latest event
    open_blockers: list[str]
    recent: list[str]
    text: str


WorkerStatus = Literal["idle", "running", "error"]


class WorkerSpec(CamelModel):
    name: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9-]*$")
    goal: str = Field(min_length=1, max_length=2000)
    owns: list[str] = Field(min_length=1, max_length=32)  # paths relative to the worker's worktree
    tools: list[str] = Field(default_factory=list, max_length=32)  # extra allowed tools, e.g. "Bash(pytest:*)"
    model: str = "sonnet"
    checks: list[str] = Field(default_factory=list, max_length=8)  # commands the verifier MUST run before it may pass the task

    @field_validator("checks")
    @classmethod
    def _checks(cls, v: list[str]) -> list[str]:
        v = [" ".join(c.split()) for c in v if c.strip()]
        if any(len(c) > 300 for c in v):
            raise ValueError("each check is one command of at most 300 characters")
        return v


class Worker(CamelModel):
    id: str
    swarm_id: str
    name: str
    goal: str
    owns: list[str]
    tools: list[str]
    model: str
    session_id: str
    checks: list[str] = Field(default_factory=list)
    status: WorkerStatus
    worktree: str | None = None
    branch: str | None = None
    turns: int = 0
    cost_usd: float = 0.0  # the CLI's own estimate; recorded, not shown (tokens are)
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache_write: int = 0
    tokens_cache_read: int = 0
    last_error: str | None = None
    pid: int | None = None
    role: Literal["worker", "verifier", "researcher", "critic"] = "worker"
    base_sha: str | None = None
    cwd: str | None = None  # verifier: the worktree it inspects
    last_activity: str | None = None  # last stream-json line seen from the running turn
    activity_note: str | None = None
    tool_sig: str | None = None
    tool_repeat: int = 0  # consecutive identical tool calls
    child_pid: int | None = None  # the `claude -p` process of the current turn
    max_gap_s: float = 0.0  # longest silence between stream lines seen so far (data for tuning monitor thresholds)
    recent_tools: list[str] = Field(default_factory=list)  # last few tool calls, e.g. "Bash: pytest -x"
    created_at: str
    updated_at: str


TaskStatus = Literal["open", "awaiting_verification", "verifying", "failed", "done", "escalated"]


class Task(CamelModel):
    id: str
    swarm_id: str
    worker: str
    goal: str
    status: TaskStatus
    rounds: int = 0  # failed verification rounds so far
    reasons: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class TurnResult(CamelModel):
    worker: str
    ok: bool
    text: str
    session_id: str
    cost_usd: float = 0.0
    tokens: dict[str, int] = Field(default_factory=dict)  # input / output / cached / total for the turns just run
    denials: list[str] = Field(default_factory=list)
    queued_only: bool = False  # True when the worker was already running and the message was queued
    budget_stopped: bool = False  # a swarm budget is used up: the message stays queued and runs when a human raises it
    turns_run: int = 0
