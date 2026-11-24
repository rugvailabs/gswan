"""Orders and their status changes."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

TRANSITIONS = {
    "new": {"paid", "cancelled"},
    "paid": {"shipped", "cancelled"},
    "shipped": {"delivered", "cancelled"},
    "delivered": set(),
    "cancelled": set(),
}


class InvalidTransition(Exception):
    pass


@dataclass(frozen=True)
class Line:
    sku: str
    name: str
    qty: int
    unit_price: Decimal
    amount: Decimal


@dataclass
class Order:
    id: str
    lines: list[Line]
    region: str
    coupon: str | None
    subtotal: Decimal
    discount: Decimal
    shipping: Decimal
    tax: Decimal
    total: Decimal
    status: str = "new"
    history: list[str] = field(default_factory=lambda: ["new"])

    def advance(self, to: str, inventory=None) -> None:
        if to not in TRANSITIONS.get(self.status, set()):
            raise InvalidTransition(f"cannot go from {self.status} to {to}")
        if to == "cancelled":
            if inventory is None:
                raise ValueError("cancelling needs the inventory to release stock")
            inventory.release([(l.sku, l.qty) for l in self.lines])
        self.status = to
        self.history.append(to)
