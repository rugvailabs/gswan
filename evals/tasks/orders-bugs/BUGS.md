# Seeded bugs (maintainers only; not copied into the agents' repo)

`repo/shop` is `reference/shop` with these 8 changes. `check/test_hidden.py` catches each one on its own.

| module | bug |
|---|---|
| money.py | rounds half-even instead of half-up |
| catalog.py | `search` lowercases the name but not the term |
| pricing.py | bulk discount from qty > 10 instead of >= 10 |
| shipping.py | `int()` instead of `ceil()` for started kg |
| checkout.py | taxes shipping |
| inventory.py | `reserve` is not all-or-nothing and reports only the first short SKU |
| orders.py | allows shipped -> cancelled |
| invoice.py | mutable default `notes=[]` leaks notes between renders and into the caller's list |
