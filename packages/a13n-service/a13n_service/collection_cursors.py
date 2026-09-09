"""Canonical envelope encoding for query-bound collection cursors."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime

from a13n_service.temporal import assume_utc, require_aware_utc

_MAX_CURSOR_LENGTH = 2048
_VERSION = "1"


class InvalidCollectionCursorError(ValueError):
    """The cursor is not a bounded, valid base64url JSON envelope."""


class CollectionCursorMismatchError(ValueError):
    """The cursor envelope does not belong to the requested collection query."""


def encode_collection_cursor(
    payload: dict[str, object],
    *,
    scope: dict[str, object],
    kind: str | None = None,
) -> str:
    """Encode feature-owned payload fields in the canonical cursor envelope."""

    envelope = dict(payload)
    envelope["v"] = _VERSION
    if kind is not None:
        envelope["kind"] = kind
    envelope["scope"] = _scope_digest(scope)
    encoded = _canonical_json(envelope)
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()


def decode_collection_cursor(
    value: str,
    *,
    scope: dict[str, object],
    kind: str | None = None,
) -> dict[str, object]:
    """Decode and validate the canonical cursor envelope."""

    if not value or len(value) > _MAX_CURSOR_LENGTH:
        raise InvalidCollectionCursorError
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
    except ValueError as error:
        raise InvalidCollectionCursorError from error
    if (
        not isinstance(payload, dict)
        or payload.get("v") != _VERSION
        or (kind is not None and payload.get("kind") != kind)
        or payload.get("scope") != _scope_digest(scope)
    ):
        raise CollectionCursorMismatchError
    return payload


def encode_time_cursor(
    timestamp: datetime,
    item_id: str,
    *,
    scope: dict[str, object],
    time_field: str = "updated_at",
    kind: str | None = None,
) -> str:
    return encode_collection_cursor(
        {time_field: assume_utc(timestamp).isoformat().replace("+00:00", "Z"), "id": item_id},
        scope=scope,
        kind=kind,
    )


def decode_time_cursor(
    value: str,
    *,
    scope: dict[str, object],
    id_prefix: str | tuple[str, ...],
    time_field: str = "updated_at",
    kind: str | None = None,
) -> tuple[datetime, str]:
    payload = decode_collection_cursor(value, scope=scope, kind=kind)
    try:
        timestamp = require_aware_utc(datetime.fromisoformat(str(payload[time_field]).replace("Z", "+00:00")))
        item_id = payload["id"]
        if not isinstance(item_id, str) or not item_id.startswith(id_prefix):
            raise InvalidCollectionCursorError
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidCollectionCursorError from error
    return timestamp, item_id


def _scope_digest(scope: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_json(scope)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


__all__ = [
    "CollectionCursorMismatchError",
    "InvalidCollectionCursorError",
    "decode_collection_cursor",
    "decode_time_cursor",
    "encode_collection_cursor",
    "encode_time_cursor",
]
