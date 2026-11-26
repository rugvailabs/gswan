"""The API. Run: `uv run uvicorn app.main:app --reload`, then open http://127.0.0.1:8000/docs."""
from __future__ import annotations

import os
import secrets
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query

from . import db
from .models import Item, ItemIn, ItemPage


def create_app(db_path: str | Path | None = None) -> FastAPI:
    path = Path(db_path or os.environ.get("APP_DB", "app.db"))
    db.init(path)
    app = FastAPI(title="API")

    def conn() -> Iterator[sqlite3.Connection]:
        yield from db.session(path)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/items", response_model=Item, status_code=201, response_model_by_alias=True)
    def create_item(body: ItemIn, c: sqlite3.Connection = Depends(conn)) -> Item:
        item = Item(id=f"item_{secrets.token_hex(6)}", name=body.name, created_at=datetime.now(timezone.utc).isoformat())
        c.execute("INSERT INTO items (id, name, created_at) VALUES (?, ?, ?)", (item.id, item.name, item.created_at))
        return item

    @app.get("/items", response_model=ItemPage, response_model_by_alias=True)
    def list_items(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                   c: sqlite3.Connection = Depends(conn)) -> ItemPage:
        total = c.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        rows = c.execute("SELECT * FROM items ORDER BY created_at, id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return ItemPage(items=[Item(**dict(r)) for r in rows], total=total, limit=limit, offset=offset)

    @app.get("/items/{item_id}", response_model=Item, response_model_by_alias=True)
    def get_item(item_id: str, c: sqlite3.Connection = Depends(conn)) -> Item:
        row = c.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "item not found")
        return Item(**dict(row))

    return app


app = create_app()
