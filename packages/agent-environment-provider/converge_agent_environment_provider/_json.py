from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue, TypeAdapter, ValidationError

_MAX_JSON_BYTES = 1024 * 1024
_MAX_JSON_DEPTH = 32
_MAX_JSON_NODES = 10_000
_JSON_ADAPTER = TypeAdapter(JsonValue)
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])


class JsonBoundaryError(ValueError):
    """A value is not bounded finite JSON."""


def detach_json(value: object) -> JsonValue:
    """Validate and recursively detach one bounded finite JSON value."""
    try:
        parsed = _JSON_ADAPTER.validate_python(value)
    except ValidationError as exc:
        raise JsonBoundaryError("value must be JSON-compatible") from exc
    _validate_shape(parsed)
    return _round_trip(parsed)


def detach_json_object(value: object) -> dict[str, JsonValue]:
    """Validate and recursively detach one bounded finite JSON object."""
    try:
        parsed = _JSON_OBJECT_ADAPTER.validate_python(value)
    except ValidationError as exc:
        raise JsonBoundaryError("value must be a JSON object") from exc
    _validate_shape(parsed)
    detached = _round_trip(parsed)
    if not isinstance(detached, dict):  # pragma: no cover - adapter invariant
        raise JsonBoundaryError("value must be a JSON object")
    return detached


def _validate_shape(value: JsonValue) -> None:
    remaining = _MAX_JSON_NODES
    stack: list[tuple[JsonValue, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        remaining -= 1
        if remaining < 0:
            raise JsonBoundaryError("JSON value contains too many nodes")
        if depth > _MAX_JSON_DEPTH:
            raise JsonBoundaryError("JSON value is nested too deeply")
        if isinstance(current, float) and not math.isfinite(current):
            raise JsonBoundaryError("JSON numbers must be finite")
        if isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
        elif isinstance(current, Mapping):
            stack.extend((item, depth + 1) for item in current.values())


def _round_trip(value: JsonValue) -> JsonValue:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise JsonBoundaryError("value must be finite JSON") from exc
    if len(encoded) > _MAX_JSON_BYTES:
        raise JsonBoundaryError("JSON value exceeds the maximum encoded size")
    return cast(JsonValue, json.loads(encoded))
