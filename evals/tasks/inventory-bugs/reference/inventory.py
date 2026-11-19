class Inventory:
    def __init__(self):
        self.items: dict[str, int] = {}

    def add(self, sku: str, qty: int) -> None:
        if qty <= 0:
            raise ValueError("qty must be > 0")
        self.items[sku] = self.items.get(sku, 0) + qty

    def remove(self, sku: str, qty: int) -> None:
        if sku not in self.items:
            raise KeyError(sku)
        if qty <= 0 or qty > self.items[sku]:
            raise ValueError("bad qty")
        self.items[sku] -= qty
        if self.items[sku] == 0:
            del self.items[sku]

    def total_units(self) -> int:
        return sum(self.items.values())

    def low_stock(self, threshold: int) -> list[str]:
        return sorted(s for s, q in self.items.items() if q < threshold)

    def merge(self, other: "Inventory") -> None:
        for s, q in list(other.items.items()):
            self.add(s, q)
