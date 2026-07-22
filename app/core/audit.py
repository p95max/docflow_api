"""Safe, bounded values for the append-only audit log."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
import json
from typing import Any


MAX_AUDIT_JSON_BYTES = 4_096
MAX_AUDIT_STRING_CHARS = 1_000
_REDACTED = "[redacted]"
_SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "secret",
    "credential",
    "authorization",
    "cookie",
    "api_key",
    "recovery_key",
)


def sanitize_audit_value(value: Any) -> Any:
    """Redact sensitive keys and cap a JSON column to a predictable size."""
    sanitized = _sanitize(value)
    encoded = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) <= MAX_AUDIT_JSON_BYTES:
        return sanitized
    preview = encoded.encode("utf-8")[: MAX_AUDIT_JSON_BYTES - 160].decode(
        "utf-8", errors="ignore"
    )
    return {
        "_truncated": True,
        "preview": f"{preview}…",
        "max_bytes": MAX_AUDIT_JSON_BYTES,
    }


def _sanitize(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _truncate_string(value)
    if isinstance(value, Enum):
        return _sanitize(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _REDACTED if _is_sensitive_key(str(key)) else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item) for item in value]
    return _truncate_string(str(value))


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _truncate_string(value: str) -> str:
    if len(value) <= MAX_AUDIT_STRING_CHARS:
        return value
    return f"{value[:MAX_AUDIT_STRING_CHARS]}…[truncated]"
