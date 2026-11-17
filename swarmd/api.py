"""FastAPI surface for the board. Bind to 127.0.0.1 only; optional bearer token."""
from __future__ import annotations

import asyncio
import hmac
from typing import AsyncIterator

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import Field
from sse_starlette.sse import EventSourceResponse

from .board import Board, NoActiveSwarm
from .models import CamelModel, Digest, Event, EventIn, EventKind, EventPage, StaleWorker, Swarm, SwarmCreate


async def event_stream(board: Board, since: int, poll: float = 0.5,
                       max_idle: float | None = None) -> AsyncIterator[dict]:
    """Yield SSE dicts for events newer than `since`. Polls SQLite so CLI/other-process posts are seen."""
    idle = 0.0
    while True:
        batch = await asyncio.to_thread(board.events_after, since)
        for e in batch:
            since = e.seq
            idle = 0.0
            yield {"id": str(e.seq), "event": "board", "data": e.model_dump_json(by_alias=True)}
        if not batch:
            if max_idle is not None and idle >= max_idle:
                return
            await asyncio.sleep(poll)
            idle += poll


class MentionIn(CamelModel):
    message: str = Field(min_length=1, max_length=8000)
    sender: str = Field(default="human", pattern=r"^[A-Za-z0-9_.-]{1,64}$")


def create_app(board: Board, token: str | None = None, tools=None, ui_dir: Path | None = None) -> FastAPI:
    """`tools` is a swarmd.mcp_server.SwarmTools (workers/status/mentions); `ui_dir` the built web UI."""
    app = FastAPI(title="swarmd", version="0.1.0")

    def auth(request: Request, authorization: str | None = Header(default=None)) -> None:
        if token is None:
            return
        given = (authorization or "").removeprefix("Bearer ").strip() or request.query_params.get("token", "")
        if not hmac.compare_digest(given, token):
            raise HTTPException(401, "invalid or missing token")

    guard = [Depends(auth)]

    @app.exception_handler(NoActiveSwarm)
    async def _no_swarm(_: Request, exc: NoActiveSwarm):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "noActiveSwarm", "message": str(exc)}, status_code=409)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/swarms", response_model=Swarm, status_code=201, dependencies=guard)
    def start_swarm(body: SwarmCreate) -> Swarm:
        return board.start_swarm(body.task)

    @app.get("/swarms/current", response_model=Swarm, dependencies=guard)
    def current_swarm() -> Swarm:
        sw = board.current_swarm()
        if sw is None:
            raise NoActiveSwarm("no active swarm")
        return sw

    @app.post("/events", response_model=Event, status_code=201, dependencies=guard)
    def post_event(body: EventIn) -> Event:
        return board.post(body)

    @app.get("/events", response_model=EventPage, dependencies=guard)
    def list_events(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                    since_seq: int = Query(0, alias="sinceSeq", ge=0),
                    kind: EventKind | None = None, agent: str | None = None) -> EventPage:
        return board.list_events(limit=limit, offset=offset, since_seq=since_seq, kind=kind, agent=agent)

    @app.get("/events/stream", dependencies=guard)
    async def stream(request: Request, since_seq: int = Query(0, alias="sinceSeq", ge=0),
                     last_event_id: str | None = Header(default=None)):
        start = int(last_event_id) if last_event_id and last_event_id.isdigit() else since_seq
        return EventSourceResponse(event_stream(board, start))

    @app.get("/digest", response_model=Digest, dependencies=guard)
    def digest(since_seq: int = Query(0, alias="sinceSeq", ge=0)) -> Digest:
        return board.digest(since_seq)

    @app.get("/stale-workers", response_model=list[StaleWorker], dependencies=guard)
    def stale(minutes: float = Query(10, gt=0)) -> list[StaleWorker]:
        return board.stale(minutes)

    if tools is not None:
        @app.get("/status", dependencies=guard)
        def status() -> dict:
            out = tools.swarm_status()
            if not out.get("ok"):
                raise NoActiveSwarm(out.get("error", "no active swarm"))
            return out

        @app.post("/workers/{name}/mentions", status_code=202, dependencies=guard)
        def mention(name: str, body: MentionIn) -> dict:
            out = tools.invoke_worker(name, body.message, sender=body.sender)
            if not out.get("ok"):
                raise HTTPException(404, out.get("error", "unknown worker"))
            return out

    if ui_dir is not None and Path(ui_dir, "index.html").exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")
    return app
