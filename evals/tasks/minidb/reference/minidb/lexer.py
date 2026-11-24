"""Tokenizer for the minidb SQL subset."""
from __future__ import annotations

from dataclasses import dataclass

from .errors import SQLError

KEYWORDS = {
    "SELECT", "DISTINCT", "FROM", "WHERE", "GROUP", "HAVING", "ORDER", "BY", "ASC", "DESC", "LIMIT", "OFFSET", "AS",
    "INSERT", "INTO", "VALUES", "UPDATE", "SET", "DELETE", "CREATE", "TABLE", "DROP", "IF", "EXISTS",
    "NOT", "NULL", "PRIMARY", "KEY", "INTEGER", "REAL", "TEXT", "AND", "OR", "IS", "IN", "BETWEEN", "LIKE", "TRUE", "FALSE",
}
TWO_CHAR = {"<=", ">=", "<>", "!=", "||"}
ONE_CHAR = set("=<>+-*/%(),;")


@dataclass(frozen=True)
class Token:
    kind: str  # KEYWORD IDENT NUMBER STRING OP EOF
    value: object
    start: int
    end: int


def tokenize(sql: str) -> list[Token]:
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j + 1
        elif c.isalpha() or c == "_":
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            word = sql[i:j]
            if word.upper() in KEYWORDS:
                out.append(Token("KEYWORD", word.upper(), i, j))
            else:
                out.append(Token("IDENT", word.lower(), i, j))
            i = j
        elif c.isdigit():
            j = i
            while j < n and sql[j].isdigit():
                j += 1
            if j < n and sql[j] == "." and j + 1 < n and sql[j + 1].isdigit():
                j += 1
                while j < n and sql[j].isdigit():
                    j += 1
                out.append(Token("NUMBER", float(sql[i:j]), i, j))
            else:
                out.append(Token("NUMBER", int(sql[i:j]), i, j))
            if j < n and (sql[j].isalpha() or sql[j] == "_"):
                raise SQLError(f"bad number at {i}")
            i = j
        elif c == "'":
            j, buf = i + 1, []
            while True:
                if j >= n:
                    raise SQLError("unterminated string")
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        buf.append("'"); j += 2; continue
                    break
                buf.append(sql[j]); j += 1
            out.append(Token("STRING", "".join(buf), i, j + 1))
            i = j + 1
        elif sql[i:i + 2] in TWO_CHAR:
            out.append(Token("OP", sql[i:i + 2], i, i + 2)); i += 2
        elif c in ONE_CHAR:
            out.append(Token("OP", c, i, i + 1)); i += 1
        else:
            raise SQLError(f"unexpected character {c!r}")
    out.append(Token("EOF", None, n, n))
    return out


def split_statements(sql: str) -> list[list[Token]]:
    """Token lists, one per non-empty statement (split on `;`), each ending with EOF."""
    stmts, cur = [], []
    for t in tokenize(sql):
        if t.kind == "EOF" or (t.kind == "OP" and t.value == ";"):
            if cur:
                stmts.append(cur + [Token("EOF", None, t.start, t.start)])
            cur = []
        else:
            cur.append(t)
    return stmts
