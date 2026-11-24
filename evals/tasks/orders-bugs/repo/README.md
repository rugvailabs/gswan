# shop — order processing (specification)

Stdlib-only Python (3.9+). All amounts are `decimal.Decimal`. This README is the specification: where the code disagrees with it, the code is wrong.

## money (`shop/money.py`)
- `to_money(x)` rounds to cents, **half up** (`0.125` -> `0.13`, `-0.125` -> `-0.13`).
- `fmt(x)` formats a rounded amount with a thousands separator and exactly 2 decimals: `1234.5` -> `1,234.50`, `-3` -> `-3.00`, `0` -> `0.00`.

## catalog (`shop/catalog.py`)
- `Catalog.from_csv(text)`: the first line must be the header `sku,name,price,weight_kg` (case-insensitive, spaces allowed). Each later non-blank line is `sku,name,price,weight_kg`; fields are stripped; SKUs are stored uppercased.
  - Any of the following raises `ValueError` starting with `line N:`: a missing field, an empty sku or name, a bad or negative number, or a duplicate SKU.
- `get(sku)`: case-insensitive, surrounding spaces ignored. An unknown SKU raises `KeyError`.
- `search(term)`: products whose name contains `term`, **case-insensitively** (the term is stripped). Sorted by lowercased name, then SKU.

## pricing (`shop/pricing.py`)
- `line_total(price, qty)` is `price * qty`, with a 10% bulk discount when `qty` is **10 or more**, then rounded to cents.
- Coupons are case-insensitive. An unknown code raises `ValueError`. Only one coupon per order.
  - `SAVE5`: 5.00 off when the subtotal is 50.00 or more.
  - `PCT10`: 10% off the subtotal.
  - `FREESHIP`: free shipping, no discount.
  - A discount is rounded to cents and never exceeds the subtotal.

## shipping (`shop/shipping.py`)
`shipping_cost(weight_kg, discounted_subtotal, coupon)`:
- 0.00 with `FREESHIP` or when the discounted subtotal is 100.00 or more.
- Otherwise by total weight:
  - up to 1 kg: 4.99
  - up to 5 kg: 9.99
  - above 5 kg: 9.99 + 1.50 for **each started kg** above 5 (5.2 kg -> 11.49, 6.0 kg -> 11.49, 6.2 kg -> 12.99)

## tax (`shop/tax.py`)
- Rates: CA 7.25%, NY 4%, OR 0%, TX 6.25%. The region is case-insensitive and stripped. An unknown region raises `ValueError`.
- Tax is charged on the **discounted subtotal only, never on shipping**, and rounded to cents.

## inventory (`shop/inventory.py`)
- `Inventory(stock)` takes SKU to count (SKUs case-insensitive). `available(sku)` gives 0 for an unknown SKU.
- `reserve(lines)` takes `(sku, qty)` pairs; the same SKU may repeat, and the quantities add up.
  - It is **all or nothing**: if any SKU is short, nothing is reserved and it raises `OutOfStock`.
  - `OutOfStock.skus` lists **every** short SKU, sorted.
- `release(lines)` puts stock back.

## orders (`shop/orders.py`)
- Status changes via `Order.advance(to, inventory=None)`:
  - new -> paid
  - new -> cancelled
  - paid -> shipped
  - paid -> cancelled
  - shipped -> delivered
- Anything else raises `InvalidTransition`. In particular, a shipped or delivered order can never be cancelled.
- Cancelling needs the inventory (`ValueError` without it) and releases the order's stock.
- `history` lists every status in order, starting with `new`.

## checkout (`shop/checkout.py`)
`checkout(catalog, inventory, cart, region, coupon=None, order_id="ORD-1")`:
- `cart` is `(sku, qty)` pairs.
- These are errors:
  - an empty cart: `ValueError`
  - a quantity that is not a positive int: `ValueError`
  - an unknown SKU: `KeyError`
- The same SKU twice is merged into one line, and lines are ordered by SKU. Bulk pricing applies to the merged quantity.
- How the order is priced:
  - `subtotal` is the sum of line totals.
  - `discount` comes from the coupon.
  - `shipping` uses the total weight and `subtotal - discount`.
  - `tax` is computed on `subtotal - discount`.
  - `total = subtotal - discount + shipping + tax`.
- Stock is reserved only after every check passed. A failed checkout reserves nothing.

## invoice (`shop/invoice.py`)
`render(order, notes=None)` returns text, in this order:
1. A header line: `Invoice <id> (<status>)`.
2. One line per order line, sorted by SKU: `f"{sku:<10}{qty:>5}{fmt(amount):>12}"`.
3. Subtotal, Discount (shown negative), Shipping, Tax and Total, each as `f"{label:<15}{fmt(value):>12}"`.
4. One `Note: <text>` line for each note: the given notes first, then `Free shipping` when shipping is 0, then `Coupon <CODE> applied` when there is a coupon.

Rendering never changes the caller's list or affects later renders. The text ends with a newline.

Tests: `python3 -m unittest discover -s tests`.
