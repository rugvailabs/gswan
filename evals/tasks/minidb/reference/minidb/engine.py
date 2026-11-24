"""Database: runs parsed statements against the store."""
from __future__ import annotations

import functools
from dataclasses import dataclass, field

from . import nodes as N
from .errors import SQLError
from .expr import Env, columns_outside_aggregates, ev, has_aggregate, sort_key, truth
from .lexer import split_statements
from .parser import parse
from .storage import Store, Table


@dataclass
class Result:
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    count: int = 0


class Database:
    def __init__(self, path: str | None = None):
        self.store = Store(path)

    def execute(self, sql: str) -> Result:
        stmts = split_statements(sql)
        if len(stmts) != 1:
            raise SQLError("execute() takes exactly one statement")
        return self._run(sql, stmts[0])

    def executescript(self, sql: str) -> list[Result]:
        return [self._run(sql, toks) for toks in split_statements(sql)]

    def _run(self, sql, toks) -> Result:
        s = parse(sql, toks)
        if isinstance(s, N.Select):
            return self.select(s)
        res = getattr(self, type(s).__name__.lower())(s)
        self.store.save()
        return res

    # ---- schema
    def createtable(self, s: N.CreateTable) -> Result:
        if s.name in self.store.tables:
            if s.if_not_exists:
                return Result()
            raise SQLError(f"table {s.name} already exists")
        names = [c.name for c in s.cols]
        if len(set(names)) != len(names):
            raise SQLError("duplicate column name")
        if sum(c.primary_key for c in s.cols) > 1:
            raise SQLError("only one PRIMARY KEY column is allowed")
        self.store.tables[s.name] = Table(s.name, s.cols)
        return Result()

    def droptable(self, s: N.DropTable) -> Result:
        if s.name not in self.store.tables:
            if s.if_exists:
                return Result()
            raise SQLError(f"no such table: {s.name}")
        del self.store.tables[s.name]
        return Result()

    # ---- changes (all-or-nothing per statement)
    def insert(self, s: N.Insert) -> Result:
        t = self.store.get(s.table)
        cols = s.cols if s.cols is not None else t.names
        if len(set(cols)) != len(cols):
            raise SQLError("duplicate column in INSERT")
        for c in cols:
            if c not in t.names:
                raise SQLError(f"no such column: {c}")
        new = []
        for vals in s.rows:
            if len(vals) != len(cols):
                raise SQLError(f"{len(cols)} values expected, got {len(vals)}")
            given = dict(zip(cols, (ev(v, Env(None, set())) for v in vals)))
            new.append([t.coerce(c, given.get(c.name)) for c in t.cols])
        t.check_unique(t.rows + new)
        t.rows.extend(new)
        return Result(count=len(new))

    def _matches(self, t: Table, where) -> list[int]:
        cols = set(t.names)
        out = []
        for i, r in enumerate(t.rows):
            if where is None or truth(ev(where, Env(dict(zip(t.names, r)), cols))) is True:
                out.append(i)
        return out

    def update(self, s: N.Update) -> Result:
        t = self.store.get(s.table)
        for c, _ in s.sets:
            if c not in t.names:
                raise SQLError(f"no such column: {c}")
        idx, cols = self._matches(t, s.where), set(t.names)
        rows = [list(r) for r in t.rows]
        for i in idx:
            env = Env(dict(zip(t.names, t.rows[i])), cols)
            vals = {c: ev(e, env) for c, e in s.sets}
            for j, cd in enumerate(t.cols):
                if cd.name in vals:
                    rows[i][j] = t.coerce(cd, vals[cd.name])
        t.check_unique(rows)
        t.rows = rows
        return Result(count=len(idx))

    def delete(self, s: N.Delete) -> Result:
        t = self.store.get(s.table)
        idx = set(self._matches(t, s.where))
        t.rows = [r for i, r in enumerate(t.rows) if i not in idx]
        return Result(count=len(idx))

    # ---- queries
    def select(self, s: N.Select) -> Result:
        if s.table is None:
            if s.items is None:
                raise SQLError("SELECT * needs FROM")
            names, rows = [], [{}]
        else:
            t = self.store.get(s.table)
            names, rows = t.names, [dict(zip(t.names, r)) for r in t.rows]
        cols = set(names)
        if s.where is not None:
            if has_aggregate(s.where):
                raise SQLError("aggregates are not allowed in WHERE")
            rows = [r for r in rows if truth(ev(s.where, Env(r, cols))) is True]
        items = s.items if s.items is not None else [N.SelectItem(N.Col(n), None, n) for n in names]
        out_names = [it.alias or (it.expr.name if isinstance(it.expr, N.Col) else it.text) for it in items]
        grouped = bool(s.group_by) or s.having is not None or any(has_aggregate(it.expr) for it in items) \
            or any(has_aggregate(e) for e, _ in s.order_by)
        if grouped:
            if s.items is None:
                raise SQLError("SELECT * cannot be used with aggregates or GROUP BY")
            for g in s.group_by:
                if g not in cols:
                    raise SQLError(f"no such column: {g}")
            for e in [it.expr for it in items] + ([s.having] if s.having is not None else []):
                for c in columns_outside_aggregates(e):
                    if c not in s.group_by:
                        raise SQLError(f"column {c} must appear in GROUP BY or be used in an aggregate")
            groups: dict[tuple, list[dict]] = {}
            for r in rows:
                groups.setdefault(tuple(r[g] for g in s.group_by), []).append(r)
            if not s.group_by and not groups:
                groups[()] = []
            units = []
            for grp in groups.values():
                env = Env(grp[0] if grp else {}, cols, grp)
                if s.having is not None and truth(ev(s.having, env)) is not True:
                    continue
                units.append(env)
        else:
            units = [Env(r, cols) for r in rows]
        outs = [tuple(ev(it.expr, u) for it in items) for u in units]

        if s.distinct:
            seen, pairs = set(), []
            for o in outs:
                if o not in seen:
                    seen.add(o); pairs.append((None, o))
        else:
            pairs = list(zip(units, outs))

        if s.order_by:
            def key_values(u, o):
                ks = []
                for e, _ in s.order_by:
                    if isinstance(e, N.Lit) and isinstance(e.value, int) and not isinstance(e.value, bool):
                        if not 1 <= e.value <= len(items):
                            raise SQLError(f"ORDER BY position {e.value} is out of range")
                        ks.append(o[e.value - 1])
                    elif isinstance(e, N.Col) and e.name in out_names:
                        ks.append(o[out_names.index(e.name)])
                    elif u is None:
                        raise SQLError("with DISTINCT, ORDER BY must name an output column")
                    else:
                        ks.append(ev(e, u))
                return ks
            keyed = [(key_values(u, o), o) for u, o in pairs]

            def cmp(a, b):
                for (e, desc), x, y in zip(s.order_by, a[0], b[0]):
                    kx, ky = sort_key(x), sort_key(y)
                    if kx != ky:
                        r = -1 if kx < ky else 1
                        return -r if desc else r
                return 0
            keyed.sort(key=functools.cmp_to_key(cmp))
            final = [o for _, o in keyed]
        else:
            final = [o for _, o in pairs]
        final = final[s.offset:]
        if s.limit is not None:
            final = final[:s.limit]
        return Result(out_names, final, len(final))
