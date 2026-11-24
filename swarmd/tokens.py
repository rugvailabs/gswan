"""Token accounting. `claude -p` reports four counts per turn; we store all four per worker and show three:
input (fresh input + cache writes), output, and cached (cache reads: usually the largest number and the cheapest).
Budgets count input + output only, so a long session re-reading its own cache does not look like new work."""
from __future__ import annotations

COLUMNS = ("tokens_in", "tokens_out", "tokens_cache_write", "tokens_cache_read")
_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
ZERO = dict.fromkeys(COLUMNS, 0)


def parse_usage(result: dict) -> dict[str, int]:
    """The `usage` block of a `claude -p` result message -> our four columns (0 for anything missing)."""
    u = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    out = {}
    for col, key in zip(COLUMNS, _USAGE_KEYS):
        try:
            out[col] = max(0, int(u.get(key) or 0))
        except (TypeError, ValueError):
            out[col] = 0
    return out


def view(tokens_in: int = 0, tokens_out: int = 0, tokens_cache_write: int = 0, tokens_cache_read: int = 0) -> dict[str, int]:
    """What the API, dashboard and report show."""
    inp = tokens_in + tokens_cache_write
    return {"input": inp, "output": tokens_out, "cached": tokens_cache_read, "total": inp + tokens_out}


def worker_view(w) -> dict[str, int]:
    return view(w.tokens_in, w.tokens_out, w.tokens_cache_write, w.tokens_cache_read)


def fmt(n: int | float) -> str:
    """1234 -> 1.2k, 1234567 -> 1.23M."""
    n = int(n)
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return f"{n / 1_000_000:.2f}M".replace(".00M", "M")


def line(v: dict[str, int]) -> str:
    return f"{fmt(v['input'])} in / {fmt(v['output'])} out" + (f" (+{fmt(v['cached'])} cached)" if v["cached"] else "")
