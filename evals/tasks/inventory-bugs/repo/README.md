# inventory
`inventory.Inventory` spec: `add(sku, qty)` (qty must be > 0 else ValueError); `remove(sku, qty)` (KeyError if sku unknown, ValueError if qty <= 0 or more than stocked; a sku that reaches 0 is deleted);
`total_units()`; `low_stock(threshold)` -> sorted skus with qty strictly below threshold; `merge(other)` adds other's quantities into self and leaves `other` unchanged.
