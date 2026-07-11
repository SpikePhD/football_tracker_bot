"""Shared redaction for logs, command errors, and administrative output."""

import os
import re

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization|bearer)\b"
    r"\s*[:=]\s*([^\s,;]+)"
)
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")


def redact_text(text: str) -> str:
    redacted = str(text or "")
    for name in ("BOT_TOKEN", "API_KEY", "LLM_API_KEY"):
        value = os.getenv(name, "")
        if value:
            redacted = redacted.replace(value, "***REDACTED***")
    redacted = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=***REDACTED***",
        redacted,
    )

    def mask_token(match: re.Match) -> str:
        value = match.group(0)
        return value if value.isdigit() else "***REDACTED_TOKEN***"

    return _LONG_TOKEN.sub(mask_token, redacted)
