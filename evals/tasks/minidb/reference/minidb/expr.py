"""Expression evaluation with SQL NULL (three-valued) semantics."""
from __future__ import annotations

import math
import re

from . import nodes as N
from .errors import SQLError

AGGREGATES = {"count", "sum", "avg", "min", "max"}


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def to_text(v) -> str:
    return repr(v) if isinstance(v, float) else str(v)


def truth(v):
    if v is None:
        return None
    if isinstance(v, str):
        raise SQLError("text used as a condition")
    return v != 0


def compare(a, b) -> int | None:
    if a is None or b is None:
        return None
    if is_num(a) and is_num(b):
        return (a > b) - (a < b)
    if isinstance(a, str) and isinstance(b, str):
        return (a > b) - (a < b)
    raise SQLError("cannot compare text and number")


def sort_key(v):
    if v is None:
        return (0, 0)
    if is_num(v):
        return (1, v)
    return (2, v)


def _arith(op, a, b):
    if a is None or b is None:
        return None
    if not (is_num(a) and is_num(b)):
        raise SQLError(f"cannot apply {op} to text")
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if b == 0:
        return None
    if op == "/":
        if isinstance(a, int) and isinstance(b, int):
            q = abs(a) // abs(b)
            return q if (a >= 0) == (b >= 0) else -q
        return a / b
    if isinstance(a, int) and isinstance(b, int):
        r = abs(a) % abs(b)
        return r if a >= 0 else -r
    return math.fmod(a, b)


def has_aggregate(e) -> bool:
    if isinstance(e, N.Func):
        return e.name in AGGREGATES or any(has_aggregate(a) for a in e.args)
    for child in children(e):
        if has_aggregate(child):
            return True
    return False


def children(e) -> list:
    if isinstance(e, N.Unary):
        return [e.e]
    if isinstance(e, N.Bin):
        return [e.l, e.r]
    if isinstance(e, N.IsNull):
        return [e.e]
    if isinstance(e, N.InList):
        return [e.e, *e.items]
    if isinstance(e, N.Between):
        return [e.e, e.lo, e.hi]
    if isinstance(e, N.Like):
        return [e.e, e.pat]
    if isinstance(e, N.Func):
        return list(e.args)
    return []


def columns_outside_aggregates(e) -> list[str]:
    if isinstance(e, N.Col):
        return [e.name]
    if isinstance(e, N.Func) and e.name in AGGREGATES:
        return []
    return [c for ch in children(e) for c in columns_outside_aggregates(ch)]


def like(text: str, pat: str) -> bool:
    rx = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in pat)
    return re.fullmatch(rx, text, re.IGNORECASE | re.DOTALL) is not None


class Env:
    """row: dict column -> value (None for no FROM). group: list of rows when evaluating aggregates."""
    def __init__(self, row: dict | None, columns: set[str], group: list[dict] | None = None):
        self.row, self.columns, self.group = row or {}, columns, group


def ev(e, env: Env):
    if isinstance(e, N.Lit):
        return e.value
    if isinstance(e, N.Col):
        if e.name not in env.columns:
            raise SQLError(f"no such column: {e.name}")
        return env.row.get(e.name)
    if isinstance(e, N.Unary):
        v = ev(e.e, env)
        if e.op == "NOT":
            t = truth(v)
            return None if t is None else int(not t)
        if v is None:
            return None
        if not is_num(v):
            raise SQLError("cannot negate text")
        return -v
    if isinstance(e, N.Bin):
        if e.op in ("AND", "OR"):
            a, b = truth(ev(e.l, env)), truth(ev(e.r, env))
            if e.op == "AND":
                if a is False or b is False:
                    return 0
                return None if a is None or b is None else 1
            if a is True or b is True:
                return 1
            return None if a is None or b is None else 0
        a, b = ev(e.l, env), ev(e.r, env)
        if e.op == "||":
            return None if a is None or b is None else to_text(a) + to_text(b)
        if e.op in ("+", "-", "*", "/", "%"):
            return _arith(e.op, a, b)
        c = compare(a, b)
        if c is None:
            return None
        return int({"=": c == 0, "!=": c != 0, "<": c < 0, "<=": c <= 0, ">": c > 0, ">=": c >= 0}[e.op])
    if isinstance(e, N.IsNull):
        v = ev(e.e, env)
        return int((v is None) != e.negated)
    if isinstance(e, N.InList):
        v = ev(e.e, env)
        if v is None:
            return None
        found, unknown = False, False
        for it in e.items:
            c = compare(v, ev(it, env))
            if c is None:
                unknown = True
            elif c == 0:
                found = True
                break
        r = 1 if found else (None if unknown else 0)
        return r if r is None or not e.negated else 1 - r
    if isinstance(e, N.Between):
        v, lo, hi = ev(e.e, env), ev(e.lo, env), ev(e.hi, env)
        if v is None or lo is None or hi is None:  # SPEC: any NULL operand of BETWEEN gives NULL
            return None
        r = int(compare(v, lo) >= 0 and compare(v, hi) <= 0)
        return 1 - r if e.negated else r
    if isinstance(e, N.Like):
        v, p = ev(e.e, env), ev(e.pat, env)
        if v is None or p is None:
            return None
        if not (isinstance(v, str) and isinstance(p, str)):
            raise SQLError("LIKE needs text")
        return int(like(v, p) != e.negated)
    if isinstance(e, N.Func):
        return func(e, env)
    raise SQLError("bad expression")


def func(e: N.Func, env: Env):
    if e.name in AGGREGATES:
        if env.group is None:
            raise SQLError(f"aggregate {e.name}() is not allowed here")
        if e.star:
            return len(env.group)
        if len(e.args) != 1:
            raise SQLError(f"{e.name}() takes one argument")
        if has_aggregate(e.args[0]):
            raise SQLError("nested aggregates are not allowed")
        vals = [ev(e.args[0], Env(r, env.columns)) for r in env.group]
        vals = [v for v in vals if v is not None]
        if e.name == "count":
            return len(vals)
        if not vals:
            return None
        if e.name in ("sum", "avg"):
            if any(not is_num(v) for v in vals):
                raise SQLError(f"{e.name}() needs numbers")
            total = sum(vals)
            return total if e.name == "sum" else total / len(vals)
        best = vals[0]
        for v in vals[1:]:
            c = compare(v, best)
            if (e.name == "min" and c < 0) or (e.name == "max" and c > 0):
                best = v
        return best
    args = [ev(a, env) for a in e.args]
    if e.name == "coalesce":
        if not args:
            raise SQLError("coalesce() needs at least one argument")
        return next((a for a in args if a is not None), None)
    if len(args) != 1:
        raise SQLError(f"{e.name}() takes one argument")
    v = args[0]
    if v is None:
        return None
    if e.name == "abs":
        if not is_num(v):
            raise SQLError("abs() needs a number")
        return abs(v)
    if not isinstance(v, str):
        raise SQLError(f"{e.name}() needs text")
    return {"lower": v.lower, "upper": v.upper, "length": lambda: len(v)}[e.name]()
