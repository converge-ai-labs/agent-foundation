"""Bounded decoding of shared telemetry value encodings."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC
from datetime import datetime as DateTime
from decimal import Decimal, InvalidOperation
from typing import cast

from pydantic import JsonValue

from .domain import Content
from .errors import TraceQueryProviderError

_MAX_CONTENT_BYTES = 1024 * 1024
_MAX_TEXT_BYTES = 4096
_MAX_USAGE_ENTRIES = 64


def io_value(value: object, mime_type: object) -> Content | None:
    if value is None:
        return None
    media_type = optional_text(mime_type, "media_type", max_bytes=256)
    if isinstance(value, str):
        if len(value.encode("utf-8")) > _MAX_CONTENT_BYTES:
            raise TraceQueryProviderError("response_too_large")
        if media_type == "application/json":
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, RecursionError) as error:
                raise TraceQueryProviderError("malformed") from error
            bounded_json(parsed, max_bytes=_MAX_CONTENT_BYTES)
            return Content(media_type=media_type, value=cast(JsonValue, parsed))
        return Content(media_type=media_type, value=value)
    bounded_json(value, max_bytes=_MAX_CONTENT_BYTES)
    return Content(media_type=media_type, value=cast(JsonValue, value))


def usage(value: object) -> Mapping[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or len(value) > _MAX_USAGE_ENTRIES:
        raise TraceQueryProviderError("malformed")
    normalized: dict[str, int] = {}
    for key, amount in value.items():
        name = required_text(key, "usage key", max_bytes=128)
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise TraceQueryProviderError("malformed")
        normalized[name] = amount
    return normalized


def decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise TraceQueryProviderError("malformed")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise TraceQueryProviderError("malformed") from error
    if not result.is_finite() or result < 0:
        raise TraceQueryProviderError("malformed")
    return result


def datetime(value: object, field: str) -> DateTime:
    if not isinstance(value, str):
        raise TraceQueryProviderError("malformed")
    try:
        parsed = DateTime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TraceQueryProviderError("malformed") from error
    if parsed.tzinfo is None:
        raise TraceQueryProviderError("malformed")
    return parsed.astimezone(UTC)


def optional_datetime(value: object, field: str) -> DateTime | None:
    return None if value is None else datetime(value, field)


def format_datetime(value: DateTime) -> str:
    if value.tzinfo is None:
        raise ValueError("Trace query timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def required_text(value: object, field: str, *, max_bytes: int = _MAX_TEXT_BYTES) -> str:
    if not isinstance(value, str) or not value:
        raise TraceQueryProviderError("malformed")
    bounded_text(value, field, max_bytes=max_bytes)
    return value


def optional_text(value: object, field: str, *, max_bytes: int = _MAX_TEXT_BYTES) -> str | None:
    return None if value is None else required_text(value, field, max_bytes=max_bytes)


def bounded_text(value: str, field: str, *, max_bytes: int = _MAX_TEXT_BYTES) -> None:
    if "\x00" in value:
        raise TraceQueryProviderError("malformed")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise TraceQueryProviderError("malformed") from error
    if len(encoded) > max_bytes:
        raise TraceQueryProviderError("response_too_large")


def bounded_json(value: object, *, max_bytes: int) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as error:
        raise TraceQueryProviderError("malformed") from error
    if len(encoded) > max_bytes:
        raise TraceQueryProviderError("response_too_large")
