class Inventory:
    def __init__(self):
        self.items: dict[str, int] = {}

    def add(self, sku: str, qty: int) -> None:
        self.items[sku] = self.items.get(sku, 0) + qty

    def remove(self, sku: str, qty: int) -> None:
        if sku not in self.items:
            raise KeyError(sku)
        self.items[sku] -= qty

    def total_units(self) -> int:
        return sum(self.items.values())

    def low_stock(self, threshold: int) -> list[str]:
        return sorted(s for s, q in self.items.items() if q <= threshold)

    def merge(self, other: "Inventory") -> None:
        for s, q in other.items.items():
            self.add(s, q)
        other.items.clear()
