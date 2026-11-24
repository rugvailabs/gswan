"""Line totals (bulk discount) and coupons."""
from __future__ import annotations
from decimal import Decimal

from .money import to_money

BULK_QTY = 10
BULK_RATE = Decimal("0.10")
COUPONS = ("SAVE5", "PCT10", "FREESHIP")


def line_total(price: Decimal, qty: int) -> Decimal:
    gross = price * qty
    if qty > BULK_QTY:
        gross -= gross * BULK_RATE
    return to_money(gross)


def normalize_coupon(code: str | None) -> str | None:
    if code is None:
        return None
    c = code.strip().upper()
    if c not in COUPONS:
        raise ValueError(f"unknown coupon {code}")
    return c


def coupon_discount(code: str | None, subtotal: Decimal) -> Decimal:
    c = normalize_coupon(code)
    if c == "SAVE5":
        d = Decimal("5") if subtotal >= 50 else Decimal("0")
    elif c == "PCT10":
        d = subtotal * Decimal("0.10")
    else:
        d = Decimal("0")
    return to_money(min(d, subtotal))
