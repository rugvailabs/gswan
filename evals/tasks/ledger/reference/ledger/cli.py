import sys
from decimal import Decimal

from .parse import parse
from .report import balance, by_category, monthly


def fmt(d: Decimal) -> str:
    return f"{d.quantize(Decimal('0.01')):.2f}"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("summary", "monthly"):
        print("error: usage: ledger summary|monthly FILE", file=sys.stderr)
        return 1
    try:
        with open(argv[1], encoding="utf-8") as f:
            entries = parse(f.read())
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if argv[0] == "summary":
        for k, v in by_category(entries).items():
            print(f"{k}: {fmt(v)}")
        print(f"total: {fmt(balance(entries))}")
    else:
        for k, v in monthly(entries).items():
            print(f"{k}: {fmt(v)}")
    return 0
