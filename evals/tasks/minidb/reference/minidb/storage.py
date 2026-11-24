"""Tables in memory, saved to a JSON file after every change."""
from __future__ import annotations

import json
import os
from dataclasses import asdict

from .errors import SQLError
from .nodes import ColumnDef


class Table:
    def __init__(self, name: str, cols: list[ColumnDef], rows: list[list] | None = None):
        self.name, self.cols, self.rows = name, cols, rows or []

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.cols]

    def coerce(self, col: ColumnDef, v):
        if v is None:
            if col.not_null or col.primary_key:
                raise SQLError(f"NOT NULL constraint failed: {self.name}.{col.name}")
            return None
        if col.type == "INTEGER":
            if isinstance(v, int) and not isinstance(v, bool):
                return v
        elif col.type == "REAL":
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        elif isinstance(v, str):
            return v
        raise SQLError(f"type mismatch: {self.name}.{col.name} is {col.type}")

    def check_unique(self, rows: list[list]) -> None:
        for i, c in enumerate(self.cols):
            if c.primary_key:
                seen = set()
                for r in rows:
                    if r[i] in seen:
                        raise SQLError(f"UNIQUE constraint failed: {self.name}.{c.name}")
                    seen.add(r[i])


class Store:
    def __init__(self, path: str | None):
        self.path, self.tables = path, {}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                for name, t in data["tables"].items():
                    self.tables[name] = Table(name, [ColumnDef(**c) for c in t["columns"]], [list(r) for r in t["rows"]])
            except (OSError, ValueError, KeyError, TypeError) as e:
                raise SQLError(f"cannot read database file: {e}") from e

    def get(self, name: str) -> Table:
        if name not in self.tables:
            raise SQLError(f"no such table: {name}")
        return self.tables[name]

    def save(self) -> None:
        if not self.path:
            return
        data = {"tables": {n: {"columns": [asdict(c) for c in t.cols], "rows": t.rows} for n, t in self.tables.items()}}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)
