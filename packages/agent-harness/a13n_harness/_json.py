"""Small shared checks for bounded Harness JSON boundaries."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from pydantic import JsonValue

_BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SENSITIVE_KEY_SEGMENTS = frozenset(
    {
        "authorization",
        "credential",
        "credentials",
        "grant",
        "grants",
        "password",
        "passwd",
        "secret",
        "token",
    }
)


def require_finite_json(value: Any, _active: set[int] | None = None) -> None:
    """Reject non-finite numbers and cycles in an otherwise JSON-shaped value."""
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return
    if not isinstance(value, list | tuple | dict):
        return

    active = _active if _active is not None else set()
    value_id = id(value)
    if value_id in active:
        raise ValueError("JSON values cannot contain cycles")
    active.add(value_id)
    try:
        items = value.values() if isinstance(value, dict) else value
        for item in items:
            require_finite_json(item, active)
    finally:
        active.remove(value_id)


def redact_bearer(value: str) -> str:
    """Remove bearer credential material from one text value."""
    return _BEARER_VALUE.sub("Bearer [REDACTED]", value)


def is_sensitive_key(key: str) -> bool:
    """Identify authority-bearing JSON fields without hiding usage token counts."""
    normalized = key.lower().replace("-", "_").replace(".", "_")
    segments = frozenset(normalized.split("_"))
    return bool(segments & _SENSITIVE_KEY_SEGMENTS) or "api_key" in normalized


def redact_json(value: JsonValue) -> JsonValue:
    """Return a detached JSON value with common authority-bearing content removed."""
    if isinstance(value, str):
        return redact_bearer(value)
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, dict):
        return {key: "[REDACTED]" if is_sensitive_key(key) else redact_json(item) for key, item in value.items()}
    return value


def dump_json_text(value: Any, *, sort_keys: bool = False) -> str:
    """Serialize strict finite JSON with the Harness canonical compact encoding."""
    require_finite_json(value)
    return json.dumps(
        value,
        sort_keys=sort_keys,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def dump_json_bytes(value: Any, *, sort_keys: bool = False) -> bytes:
    """Serialize canonical strict JSON as UTF-8 bytes."""
    return dump_json_text(value, sort_keys=sort_keys).encode("utf-8")
