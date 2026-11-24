"""Turn a cart into an order."""
from __future__ import annotations

from decimal import Decimal

from .money import to_money
from .orders import Line, Order
from .pricing import coupon_discount, line_total, normalize_coupon
from .shipping import shipping_cost
from .tax import tax_for


def checkout(catalog, inventory, cart, region: str, coupon: str | None = None, order_id: str = "ORD-1") -> Order:
    if not cart:
        raise ValueError("cart is empty")
    qty: dict[str, int] = {}
    for sku, q in cart:
        if not isinstance(q, int) or isinstance(q, bool) or q <= 0:
            raise ValueError(f"bad quantity for {sku}: {q!r}")
        p = catalog.get(sku)
        qty[p.sku] = qty.get(p.sku, 0) + q
    code = normalize_coupon(coupon)
    lines = []
    for sku in sorted(qty):
        p = catalog.get(sku)
        lines.append(Line(p.sku, p.name, qty[sku], p.price, line_total(p.price, qty[sku])))
    subtotal = to_money(sum((l.amount for l in lines), Decimal("0")))
    discount = coupon_discount(code, subtotal) if code else Decimal("0.00")
    discounted = subtotal - discount
    weight = sum((catalog.get(l.sku).weight_kg * l.qty for l in lines), Decimal("0"))
    shipping = to_money(shipping_cost(weight, discounted, code))
    tax = tax_for(region, discounted + shipping)
    inventory.reserve([(l.sku, l.qty) for l in lines])
    return Order(order_id, lines, region.strip().upper(), code, subtotal, discount, shipping, tax,
                 to_money(discounted + shipping + tax))
