"""Strip likely secrets from board text before it is stored (design §4)."""
import re

_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]
_KV = re.compile(r"(?i)\b([A-Za-z0-9_]*(?:secret|token|password|passwd|api[_-]?key)[A-Za-z0-9_]*)\s*[=:]\s*(\S+)")


def redact(text: str) -> str:
    for pat in _PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return _KV.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
