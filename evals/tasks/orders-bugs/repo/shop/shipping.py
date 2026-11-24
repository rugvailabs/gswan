"""Shipping by weight, free over a threshold."""
from __future__ import annotations
import math
from decimal import Decimal

FREE_FROM = Decimal("100")
SMALL, MEDIUM, PER_KG = Decimal("4.99"), Decimal("9.99"), Decimal("1.50")


def shipping_cost(weight_kg: Decimal, discounted_subtotal: Decimal, coupon: str | None = None) -> Decimal:
    if coupon == "FREESHIP" or discounted_subtotal >= FREE_FROM:
        return Decimal("0.00")
    if weight_kg <= 1:
        return SMALL
    if weight_kg <= 5:
        return MEDIUM
    return MEDIUM + PER_KG * int(weight_kg - 5)
