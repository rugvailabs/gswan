"""Sales tax by region."""
from __future__ import annotations
from decimal import Decimal

from .money import to_money

RATES = {"CA": Decimal("0.0725"), "NY": Decimal("0.04"), "OR": Decimal("0"), "TX": Decimal("0.0625")}


def tax_for(region: str, taxable: Decimal) -> Decimal:
    r = region.strip().upper()
    if r not in RATES:
        raise ValueError(f"unknown region {region}")
    return to_money(taxable * RATES[r])
