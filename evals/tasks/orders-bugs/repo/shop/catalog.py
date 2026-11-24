"""Product catalog loaded from CSV."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

HEADER = ["sku", "name", "price", "weight_kg"]


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: Decimal
    weight_kg: Decimal


class Catalog:
    def __init__(self, products: list[Product] | None = None):
        self._p: dict[str, Product] = {}
        for p in products or []:
            self.add(p)

    def add(self, p: Product) -> None:
        if p.sku in self._p:
            raise ValueError(f"duplicate sku {p.sku}")
        self._p[p.sku] = p

    @classmethod
    def from_csv(cls, text: str) -> "Catalog":
        cat = cls()
        lines = text.splitlines()
        if not lines or [h.strip().lower() for h in lines[0].split(",")] != HEADER:
            raise ValueError("line 1: expected header sku,name,price,weight_kg")
        for n, line in enumerate(lines[1:], start=2):
            if not line.strip():
                continue
            parts = [x.strip() for x in line.split(",")]
            if len(parts) != 4 or not parts[0] or not parts[1]:
                raise ValueError(f"line {n}: expected 4 fields")
            try:
                price, weight = Decimal(parts[2]), Decimal(parts[3])
            except InvalidOperation:
                raise ValueError(f"line {n}: bad number") from None
            if price < 0 or weight < 0:
                raise ValueError(f"line {n}: negative number")
            try:
                cat.add(Product(parts[0].upper(), parts[1], price, weight))
            except ValueError as e:
                raise ValueError(f"line {n}: {e}") from None
        return cat

    def get(self, sku: str) -> Product:
        try:
            return self._p[sku.strip().upper()]
        except KeyError:
            raise KeyError(f"unknown sku {sku}") from None

    def search(self, term: str) -> list[Product]:
        t = term.strip()
        hits = [p for p in self._p.values() if t in p.name.lower()]
        return sorted(hits, key=lambda p: (p.name.lower(), p.sku))
