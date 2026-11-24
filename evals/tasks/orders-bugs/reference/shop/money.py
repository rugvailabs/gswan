"""Money helpers: Decimal amounts rounded to cents, half up."""
from __future__ import annotations
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def to_money(x) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def fmt(x) -> str:
    d = to_money(x)
    return ("-" if d < 0 else "") + format(abs(d), ",.2f")
