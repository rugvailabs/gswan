"""Decision obligations: the coordinator must say WHY on the board, not just what.

Some choices are detectable from swarm state, so they are enforced instead of left to the coordinator's goodwill:
  split               workers were defined           -> why the task was split this way
  escalation <worker> a task was escalated           -> what was done about it / what the human was told
  critic <target>     critic blockers are still open -> how each was resolved, or why it was overridden
                      (target `plan` = plan critic; a worker = its task passed with blockers open in the last critic round)
A `decision` event from the coordinator (or a human) whose body starts with `[topic]` / `[topic subject]` and is newer than
the thing it explains settles the obligation. `merge_workers` and the Stop hook refuse to finish while any is missing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .board import Board
from .critics import Critiques
from .models import Event, EventIn
from .tasks import Tasks
from .workers import Registry

TOPICS = ("split", "escalation", "critic", "other")
NEEDS_SUBJECT = ("escalation", "critic")
AUTHORS = ("coordinator", "human")
MIN_WHY = 20  # characters; "ok" is not a reason
TAG = re.compile(r"^\[(split|escalation|critic|other)(?: ([A-Za-z0-9_.-]+))?\]\s*(.*)", re.S)


class DecisionError(Exception):
    pass


@dataclass(frozen=True)
class Obligation:
    topic: str
    subject: str  # worker name, `plan`, or "" for split
    ask: str      # what the decision has to explain
    since: str    # ts of the thing being explained; only a later decision settles it

    @property
    def tag(self) -> str:
        return f"[{self.topic} {self.subject}]" if self.subject else f"[{self.topic}]"

    def view(self) -> dict:
        return {"topic": self.topic, "subject": self.subject, "tag": self.tag, "ask": self.ask}


def required(board: Board) -> list[Obligation]:
    tasks, crit, out = Tasks(board), Critiques(board), []
    workers = [w for w in Registry(board).list() if w.name != "integrator"]
    if workers:
        out.append(Obligation("split", "", f"why the task was split into {', '.join(w.name for w in workers)} with these owned paths", ""))
    done = set()
    for t in tasks.list():
        if t.status == "escalated":
            out.append(Obligation("escalation", t.worker, f"what you did about {t.worker} being escalated and what the user was told", t.updated_at))
        elif t.status == "done":
            done.add(t.worker)
    for target in ["plan", *sorted(done)]:
        rnd = crit.rounds(target)
        open_b = crit.blockers(target, rnd) if rnd else []
        if open_b:
            what = "how each plan-critic blocker was fixed, or why it was ignored" if target == "plan" else \
                f"why {target} was accepted with critic blocker(s) still open after the last critic round"
            out.append(Obligation("critic", target, f"{what} ({len(open_b)} blocker(s))", max(f.ts for f in open_b)))
    return out


def recorded(board: Board) -> list[tuple[str, str, str]]:
    """(topic, subject, ts) of every well-formed decision by the coordinator or a human."""
    out = []
    for e in board.list_events(kind="decision", limit=500).items:
        m = TAG.match(e.body)
        if m and e.agent in AUTHORS and len(m.group(3).strip()) >= MIN_WHY:
            out.append((m.group(1), m.group(2) or "", e.ts))
    return out


def missing(board: Board) -> list[Obligation]:
    have = recorded(board)
    return [o for o in required(board) if not any(t == o.topic and s == o.subject and ts >= o.since for t, s, ts in have)]


def record(board: Board, topic: str, why: str, subject: str = "", agent: str = "coordinator") -> Event:
    why, subject = why.strip(), subject.strip()
    if topic not in TOPICS:
        raise DecisionError(f"topic must be one of {', '.join(TOPICS)}")
    if topic in NEEDS_SUBJECT and not subject:
        raise DecisionError(f"a `{topic}` decision needs a subject (the worker name, or `plan` for the plan critic)")
    if len(why) < MIN_WHY:
        raise DecisionError(f"`why` must be a real reason (at least {MIN_WHY} characters): what you chose, and why over the alternative")
    tag = f"[{topic} {subject}]" if subject else f"[{topic}]"
    return board.post(EventIn(agent=agent, kind="decision", body=f"{tag} {why}"))


def owed_text(owed: list[Obligation]) -> str:
    return "; ".join(f"{o.tag} {o.ask}" for o in owed)
