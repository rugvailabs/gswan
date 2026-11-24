import datetime
import re
from decimal import Decimal

from .model import Entry

AMOUNT = re.compile(r"-?\d+(\.\d{1,2})?")


def parse(text: str) -> list[Entry]:
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 4:
            raise ValueError(f"line {n}: expected 4 fields separated by '|', got {len(parts)}")
        d, desc, amt, cat = parts
        try:
            date = datetime.date.fromisoformat(d)
        except ValueError:
            raise ValueError(f"line {n}: bad date {d!r}") from None
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise ValueError(f"line {n}: bad date {d!r}")
        if not desc:
            raise ValueError(f"line {n}: empty description")
        if not AMOUNT.fullmatch(amt):
            raise ValueError(f"line {n}: bad amount {amt!r}")
        if not cat:
            raise ValueError(f"line {n}: empty category")
        out.append(Entry(date, desc, Decimal(amt), cat.lower()))
    return out
