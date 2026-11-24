"""Recursive-descent parser: one statement's tokens -> AST."""
from __future__ import annotations

from . import nodes as N
from .errors import SQLError
from .lexer import Token

COMPARE = {"=", "!=", "<>", "<", "<=", ">", ">="}
FUNCS = {"count", "sum", "avg", "min", "max", "lower", "upper", "length", "abs", "coalesce"}


class Parser:
    def __init__(self, sql: str, toks: list[Token]):
        self.sql, self.t, self.i = sql, toks, 0

    # ---- helpers
    @property
    def cur(self) -> Token:
        return self.t[self.i]

    def at(self, kind: str, value=None) -> bool:
        c = self.cur
        return c.kind == kind and (value is None or c.value == value)

    def kw(self, *words: str) -> bool:
        return self.cur.kind == "KEYWORD" and self.cur.value in words

    def op(self, *ops: str) -> bool:
        return self.cur.kind == "OP" and self.cur.value in ops

    def take(self) -> Token:
        tok = self.t[self.i]; self.i += 1
        return tok

    def expect_kw(self, word: str) -> None:
        if not self.kw(word):
            raise SQLError(f"expected {word} near {self.near()}")
        self.take()

    def expect_op(self, o: str) -> None:
        if not self.op(o):
            raise SQLError(f"expected {o!r} near {self.near()}")
        self.take()

    def ident(self) -> str:
        if not self.at("IDENT"):
            raise SQLError(f"expected a name near {self.near()}")
        return self.take().value

    def near(self) -> str:
        return "end of input" if self.cur.kind == "EOF" else repr(self.sql[self.cur.start:self.cur.end])

    def int_lit(self, what: str) -> int:
        if not (self.at("NUMBER") and isinstance(self.cur.value, int)):
            raise SQLError(f"{what} must be a non-negative integer")
        return self.take().value

    # ---- statements
    def statement(self):
        if self.kw("SELECT"):
            s = self.select()
        elif self.kw("INSERT"):
            s = self.insert()
        elif self.kw("CREATE"):
            s = self.create()
        elif self.kw("DROP"):
            s = self.drop()
        elif self.kw("UPDATE"):
            s = self.update()
        elif self.kw("DELETE"):
            s = self.delete()
        else:
            raise SQLError(f"unknown statement near {self.near()}")
        if not self.at("EOF"):
            raise SQLError(f"unexpected {self.near()}")
        return s

    def create(self):
        self.take(); self.expect_kw("TABLE")
        ine = False
        if self.kw("IF"):
            self.take(); self.expect_kw("NOT"); self.expect_kw("EXISTS"); ine = True
        name = self.ident(); self.expect_op("(")
        cols = []
        while True:
            cname = self.ident()
            if not self.kw("INTEGER", "REAL", "TEXT"):
                raise SQLError(f"expected a column type near {self.near()}")
            cd = N.ColumnDef(cname, self.take().value)
            while True:
                if self.kw("NOT"):
                    self.take(); self.expect_kw("NULL"); cd.not_null = True
                elif self.kw("PRIMARY"):
                    self.take(); self.expect_kw("KEY"); cd.primary_key = True
                else:
                    break
            cols.append(cd)
            if self.op(","):
                self.take(); continue
            break
        self.expect_op(")")
        return N.CreateTable(name, cols, ine)

    def drop(self):
        self.take(); self.expect_kw("TABLE")
        ie = False
        if self.kw("IF"):
            self.take(); self.expect_kw("EXISTS"); ie = True
        return N.DropTable(self.ident(), ie)

    def insert(self):
        self.take(); self.expect_kw("INTO")
        table, cols = self.ident(), None
        if self.op("("):
            self.take(); cols = [self.ident()]
            while self.op(","):
                self.take(); cols.append(self.ident())
            self.expect_op(")")
        self.expect_kw("VALUES")
        rows = []
        while True:
            self.expect_op("(")
            row = [self.expr()]
            while self.op(","):
                self.take(); row.append(self.expr())
            self.expect_op(")")
            rows.append(row)
            if self.op(","):
                self.take(); continue
            break
        return N.Insert(table, cols, rows)

    def update(self):
        self.take(); table = self.ident(); self.expect_kw("SET")
        sets = []
        while True:
            c = self.ident(); self.expect_op("="); sets.append((c, self.expr()))
            if self.op(","):
                self.take(); continue
            break
        where = None
        if self.kw("WHERE"):
            self.take(); where = self.expr()
        return N.Update(table, sets, where)

    def delete(self):
        self.take(); self.expect_kw("FROM"); table = self.ident()
        where = None
        if self.kw("WHERE"):
            self.take(); where = self.expr()
        return N.Delete(table, where)

    def select(self):
        self.take()
        distinct = False
        if self.kw("DISTINCT"):
            self.take(); distinct = True
        items = None
        if self.op("*"):
            self.take()
        else:
            items = [self.select_item()]
            while self.op(","):
                self.take(); items.append(self.select_item())
        s = N.Select(distinct, items, None)
        if self.kw("FROM"):
            self.take(); s.table = self.ident()
        if self.kw("WHERE"):
            self.take(); s.where = self.expr()
        if self.kw("GROUP"):
            self.take(); self.expect_kw("BY")
            s.group_by = [self.ident()]
            while self.op(","):
                self.take(); s.group_by.append(self.ident())
        if self.kw("HAVING"):
            self.take(); s.having = self.expr()
        if self.kw("ORDER"):
            self.take(); self.expect_kw("BY")
            while True:
                e, desc = self.expr(), False
                if self.kw("ASC", "DESC"):
                    desc = self.take().value == "DESC"
                s.order_by.append((e, desc))
                if self.op(","):
                    self.take(); continue
                break
        if self.kw("LIMIT"):
            self.take(); s.limit = self.int_lit("LIMIT")
            if self.kw("OFFSET"):
                self.take(); s.offset = self.int_lit("OFFSET")
        return s

    def select_item(self):
        start = self.cur.start
        e = self.expr()
        text = self.sql[start:self.t[self.i - 1].end].strip()
        alias = None
        if self.kw("AS"):
            self.take(); alias = self.ident()
        return N.SelectItem(e, alias, text)

    # ---- expressions
    def expr(self):
        return self.or_()

    def or_(self):
        e = self.and_()
        while self.kw("OR"):
            self.take(); e = N.Bin("OR", e, self.and_())
        return e

    def and_(self):
        e = self.not_()
        while self.kw("AND"):
            self.take(); e = N.Bin("AND", e, self.not_())
        return e

    def not_(self):
        if self.kw("NOT"):
            self.take(); return N.Unary("NOT", self.not_())
        return self.cmp()

    def cmp(self):
        e = self.add()
        if self.op(*COMPARE):
            o = self.take().value
            return N.Bin("!=" if o == "<>" else o, e, self.add())
        if self.kw("IS"):
            self.take(); neg = False
            if self.kw("NOT"):
                self.take(); neg = True
            self.expect_kw("NULL")
            return N.IsNull(e, neg)
        neg = False
        if self.kw("NOT"):
            self.take(); neg = True
            if not self.kw("IN", "BETWEEN", "LIKE"):
                raise SQLError(f"expected IN, BETWEEN or LIKE near {self.near()}")
        if self.kw("IN"):
            self.take(); self.expect_op("(")
            items = [self.expr()]
            while self.op(","):
                self.take(); items.append(self.expr())
            self.expect_op(")")
            return N.InList(e, items, neg)
        if self.kw("BETWEEN"):
            self.take(); lo = self.add(); self.expect_kw("AND"); hi = self.add()
            return N.Between(e, lo, hi, neg)
        if self.kw("LIKE"):
            self.take(); return N.Like(e, self.add(), neg)
        return e

    def add(self):
        e = self.mul()
        while self.op("+", "-", "||"):
            o = self.take().value; e = N.Bin(o, e, self.mul())
        return e

    def mul(self):
        e = self.unary()
        while self.op("*", "/", "%"):
            o = self.take().value; e = N.Bin(o, e, self.unary())
        return e

    def unary(self):
        if self.op("-"):
            self.take(); return N.Unary("-", self.unary())
        if self.op("+"):
            self.take(); return self.unary()
        return self.primary()

    def primary(self):
        c = self.cur
        if c.kind in ("NUMBER", "STRING"):
            self.take(); return N.Lit(c.value)
        if self.kw("NULL"):
            self.take(); return N.Lit(None)
        if self.kw("TRUE", "FALSE"):
            self.take(); return N.Lit(1 if c.value == "TRUE" else 0)
        if self.op("("):
            self.take(); e = self.expr(); self.expect_op(")"); return e
        if c.kind == "IDENT":
            self.take()
            if not self.op("("):
                return N.Col(c.value)
            if c.value not in FUNCS:
                raise SQLError(f"no such function: {c.value}")
            self.take()
            if self.op("*"):
                if c.value != "count":
                    raise SQLError(f"{c.value}(*) is not allowed")
                self.take(); self.expect_op(")")
                return N.Func("count", [], star=True)
            args = []
            if not self.op(")"):
                args.append(self.expr())
                while self.op(","):
                    self.take(); args.append(self.expr())
            self.expect_op(")")
            return N.Func(c.value, args)
        raise SQLError(f"unexpected {self.near()}")


def parse(sql: str, toks: list[Token]):
    return Parser(sql, toks).statement()
