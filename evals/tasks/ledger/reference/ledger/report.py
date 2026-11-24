from decimal import Decimal


def balance(entries) -> Decimal:
    return sum((e.amount for e in entries), Decimal("0"))


def by_category(entries) -> dict:
    out: dict = {}
    for e in entries:
        out[e.category] = out.get(e.category, Decimal("0")) + e.amount
    return dict(sorted(out.items()))


def monthly(entries) -> dict:
    out: dict = {}
    for e in entries:
        k = e.date.strftime("%Y-%m")
        out[k] = out.get(k, Decimal("0")) + e.amount
    return dict(sorted(out.items()))
