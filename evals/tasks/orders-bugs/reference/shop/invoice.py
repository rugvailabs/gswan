"""Plain-text invoices."""
from __future__ import annotations

from .money import fmt


def render(order, notes: list[str] | None = None) -> str:
    notes = list(notes or [])
    if order.shipping == 0:
        notes.append("Free shipping")
    if order.coupon:
        notes.append(f"Coupon {order.coupon} applied")
    out = [f"Invoice {order.id} ({order.status})"]
    for l in sorted(order.lines, key=lambda l: l.sku):
        out.append(f"{l.sku:<10}{l.qty:>5}{fmt(l.amount):>12}")
    for label, v in (("Subtotal", order.subtotal), ("Discount", -order.discount), ("Shipping", order.shipping),
                     ("Tax", order.tax), ("Total", order.total)):
        out.append(f"{label:<15}{fmt(v):>12}")
    out.extend(f"Note: {n}" for n in notes)
    return "\n".join(out) + "\n"
