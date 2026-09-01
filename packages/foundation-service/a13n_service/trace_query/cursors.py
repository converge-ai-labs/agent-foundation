"""Opaque caller- and query-bound Trace Query cursors."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

_MAX_CURSOR_BYTES = 8192
_MAX_PROVIDER_CURSOR_BYTES = 4096


class TraceCursorError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DecodedTraceCursor:
    provider_cursor: str
    from_started_at: datetime
    to_started_at: datetime


def encode_trace_cursor(
    *,
    provider_cursor: str,
    scope: dict[str, object],
    from_started_at: datetime,
    to_started_at: datetime,
) -> str:
    if not provider_cursor or len(provider_cursor.encode("utf-8")) > _MAX_PROVIDER_CURSOR_BYTES:
        raise TraceCursorError("provider cursor is invalid")
    start = _format_datetime(from_started_at)
    end = _format_datetime(to_started_at)
    payload = {
        "v": "1",
        "provider_cursor": provider_cursor,
        "from": start,
        "to": end,
        "scope": _scope_digest(scope, start=start, end=end),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    cursor = base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()
    if len(cursor.encode("ascii")) > _MAX_CURSOR_BYTES:
        raise TraceCursorError("cursor exceeds its supported size")
    return cursor


def decode_trace_cursor(value: str, *, scope: dict[str, object]) -> DecodedTraceCursor:
    if not value or len(value.encode("utf-8")) > _MAX_CURSOR_BYTES:
        raise TraceCursorError("invalid cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if not isinstance(payload, dict) or payload.get("v") != "1":
            raise TraceCursorError("invalid cursor")
        start_text = payload["from"]
        end_text = payload["to"]
        if not isinstance(start_text, str) or not isinstance(end_text, str):
            raise TraceCursorError("invalid cursor range")
        if payload.get("scope") != _scope_digest(scope, start=start_text, end=end_text):
            raise TraceCursorError("cursor does not match this query")
        provider_cursor = payload["provider_cursor"]
        if (
            not isinstance(provider_cursor, str)
            or not provider_cursor
            or len(provider_cursor.encode("utf-8")) > _MAX_PROVIDER_CURSOR_BYTES
        ):
            raise TraceCursorError("invalid provider cursor")
        start = _parse_datetime(start_text)
        end = _parse_datetime(end_text)
    except (KeyError, TypeError, ValueError, UnicodeEncodeError, json.JSONDecodeError) as error:
        if isinstance(error, TraceCursorError):
            raise
        raise TraceCursorError("invalid cursor") from error
    return DecodedTraceCursor(provider_cursor, start, end)


def _scope_digest(scope: dict[str, object], *, start: str, end: str) -> str:
    encoded = json.dumps(
        {"scope": scope, "from": start, "to": end},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        raise TraceCursorError("cursor timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise TraceCursorError("cursor timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise TraceCursorError("cursor timestamp must be timezone-aware")
    return parsed.astimezone(UTC)
