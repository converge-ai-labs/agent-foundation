"""Small shared checks for bounded Harness JSON boundaries."""

from __future__ import annotations

import json
import math
import re
from dataclasses import is_dataclass
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, JsonValue, TypeAdapter

_ANY_ADAPTER = TypeAdapter(Any)
_JSON_ADAPTER = TypeAdapter(JsonValue)

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


@lru_cache(maxsize=128)
def _runtime_adapter(value_type: type[Any]) -> TypeAdapter[Any]:
    return TypeAdapter(value_type)


def project_json(value: Any, *, adapter: TypeAdapter[Any] | None = None) -> JsonValue:
    """Project a typed value before enforcing the finite JSON boundary.

    Prefer the owning contract's adapter. Native toolsets expose only argument
    validators, so their process-local values use runtime structured serializers
    inside ordinary containers; erased Annotated metadata cannot be recovered.
    """
    require_finite_json(value)
    projected = (
        adapter.dump_python(value, mode="json", warnings="error")
        if adapter is not None
        else _project_runtime_value(value)
    )
    require_finite_json(projected)
    return _JSON_ADAPTER.validate_python(projected, strict=True)


def _project_runtime_value(value: Any) -> Any:
    if isinstance(value, BaseModel) or (is_dataclass(value) and not isinstance(value, type)):
        projected = _runtime_adapter(type(value)).dump_python(value, mode="json", warnings="error")
        require_finite_json(projected)
        return projected
    if isinstance(value, dict):
        value = {key: _project_runtime_value(item) for key, item in value.items()}
    elif isinstance(value, list | tuple):
        value = [_project_runtime_value(item) for item in value]
    return _ANY_ADAPTER.dump_python(value, mode="json", warnings="error")


def redact_bearer(value: str) -> str:
    """Remove bearer credential material from one text value."""
    return _BEARER_VALUE.sub("Bearer [REDACTED]", value)


def is_sensitive_key(key: str) -> bool:
    """Identify authority-bearing JSON fields without hiding usage token counts."""
    normalized = key.lower().replace("-", "_").replace(".", "_")
    segments = frozenset(normalized.split("_"))
    return bool(segments & _SENSITIVE_KEY_SEGMENTS) or "api_key" in normalized


def redact_json(value: JsonValue) -> JsonValue:
    """Return a detached JSON value with common authority-bearing content removed.

    Object members whose key names an authority, such as `authorization`, `api_key`, `password`, `secret` or
    `token` (but not token counts such as `input_tokens`), become `"[REDACTED]"`, and every string has its
    `Bearer <credential>` values replaced. This is the rule the Harness applies to extension event payloads;
    Hosts apply it to trace content they read back from a collector.
    """
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
