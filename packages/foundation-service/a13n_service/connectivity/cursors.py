"""Opaque, query-bound cursors shared by Connectivity collections."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime


class CursorError(ValueError):
    pass


def encode_cursor(*, updated_at: datetime, object_id: str, scope: dict[str, object]) -> str:
    payload = {
        "v": "1",
        "updated_at": _utc(updated_at).isoformat().replace("+00:00", "Z"),
        "id": object_id,
        "scope": _scope_digest(scope),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()


def decode_cursor(value: str, *, scope: dict[str, object], id_prefix: str) -> tuple[datetime, str]:
    if not value or len(value) > 2048:
        raise CursorError("invalid cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if not isinstance(payload, dict) or payload.get("v") != "1" or payload.get("scope") != _scope_digest(scope):
            raise CursorError("cursor does not match this query")
        updated_at = datetime.fromisoformat(str(payload["updated_at"]).replace("Z", "+00:00"))
        object_id = payload["id"]
        if not isinstance(object_id, str) or not object_id.startswith(f"{id_prefix}_"):
            raise CursorError("invalid cursor")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        if isinstance(error, CursorError):
            raise
        raise CursorError("invalid cursor") from error
    return _utc(updated_at), object_id


def _scope_digest(scope: dict[str, object]) -> str:
    encoded = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
