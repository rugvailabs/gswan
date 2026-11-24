"""`python3 -m minidb DBFILE [SQL]`: run statements (from SQL or stdin) and print SELECT results."""
from __future__ import annotations

import sys

from .engine import Database
from .errors import SQLError
from .lexer import split_statements


def fmt(v) -> str:
    if v is None:
        return "NULL"
    return repr(v) if isinstance(v, float) else str(v)


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        print("usage: python3 -m minidb DBFILE [SQL]", file=sys.stderr)
        return 2
    path = None if argv[0] == ":memory:" else argv[0]
    sql = argv[1] if len(argv) == 2 else sys.stdin.read()
    try:
        db = Database(path)
        stmts = split_statements(sql)
    except SQLError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    rc = 0
    for toks in stmts:
        try:
            res = db._run(sql, toks)
        except SQLError as e:
            print(f"error: {e}", file=sys.stderr)
            rc = 1
            continue
        if res.columns:
            print("|".join(res.columns))
            for r in res.rows:
                print("|".join(fmt(v) for v in r))
    return rc
