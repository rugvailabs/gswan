def truncate(s: str, n: int, suffix: str = "…") -> str:
    if n <= 0:
        raise ValueError("n must be positive")
    if len(s) <= n:
        return s
    if n < len(suffix):
        return suffix[:n]
    return s[: n - len(suffix)] + suffix
