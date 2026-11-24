"""AST nodes."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Lit:
    value: object


@dataclass
class Col:
    name: str


@dataclass
class Unary:
    op: str  # '-' or 'NOT'
    e: object


@dataclass
class Bin:
    op: str
    l: object
    r: object


@dataclass
class IsNull:
    e: object
    negated: bool


@dataclass
class InList:
    e: object
    items: list
    negated: bool


@dataclass
class Between:
    e: object
    lo: object
    hi: object
    negated: bool


@dataclass
class Like:
    e: object
    pat: object
    negated: bool


@dataclass
class Func:
    name: str
    args: list
    star: bool = False


@dataclass
class ColumnDef:
    name: str
    type: str
    not_null: bool = False
    primary_key: bool = False


@dataclass
class CreateTable:
    name: str
    cols: list
    if_not_exists: bool


@dataclass
class DropTable:
    name: str
    if_exists: bool


@dataclass
class Insert:
    table: str
    cols: list | None
    rows: list


@dataclass
class SelectItem:
    expr: object
    alias: str | None
    text: str


@dataclass
class Select:
    distinct: bool
    items: list | None  # None = *
    table: str | None
    where: object = None
    group_by: list = field(default_factory=list)
    having: object = None
    order_by: list = field(default_factory=list)  # [(expr, desc)]
    limit: int | None = None
    offset: int = 0


@dataclass
class Update:
    table: str
    sets: list  # [(col, expr)]
    where: object


@dataclass
class Delete:
    table: str
    where: object
