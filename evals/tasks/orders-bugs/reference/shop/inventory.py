"""Stock levels with all-or-nothing reservations."""
from __future__ import annotations


class OutOfStock(Exception):
    def __init__(self, skus):
        self.skus = sorted(skus)
        super().__init__("out of stock: " + ", ".join(self.skus))


class Inventory:
    def __init__(self, stock: dict[str, int]):
        self._s = {k.strip().upper(): int(v) for k, v in stock.items()}

    def available(self, sku: str) -> int:
        return self._s.get(sku.strip().upper(), 0)

    @staticmethod
    def _totals(lines) -> dict[str, int]:
        need: dict[str, int] = {}
        for sku, qty in lines:
            k = sku.strip().upper()
            need[k] = need.get(k, 0) + qty
        return need

    def reserve(self, lines) -> None:
        need = self._totals(lines)
        short = [k for k, q in need.items() if self._s.get(k, 0) < q]
        if short:
            raise OutOfStock(short)
        for k, q in need.items():
            self._s[k] -= q

    def release(self, lines) -> None:
        for k, q in self._totals(lines).items():
            self._s[k] = self._s.get(k, 0) + q
