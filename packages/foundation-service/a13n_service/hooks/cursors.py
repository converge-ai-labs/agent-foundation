"""Opaque, query-bound HookSubscription collection cursors."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime


class HookCursorError(ValueError):
    pass


def encode_hook_cursor(*, updated_at: datetime, subscription_id: str, scope: dict[str, object]) -> str:
    payload = {
        "v": "1",
        "updated_at": _utc(updated_at).isoformat().replace("+00:00", "Z"),
        "id": subscription_id,
        "scope": _scope_digest(scope),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()


def decode_hook_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    if not value or len(value) > 2048:
        raise HookCursorError("invalid cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if not isinstance(payload, dict) or payload.get("v") != "1" or payload.get("scope") != _scope_digest(scope):
            raise HookCursorError("cursor does not match this collection")
        updated_at = datetime.fromisoformat(str(payload["updated_at"]).replace("Z", "+00:00"))
        subscription_id = payload["id"]
        if not isinstance(subscription_id, str) or not subscription_id.startswith("hsub_"):
            raise HookCursorError("invalid cursor")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        if isinstance(error, HookCursorError):
            raise
        raise HookCursorError("invalid cursor") from error
    return _utc(updated_at), subscription_id


def _scope_digest(scope: dict[str, object]) -> str:
    encoded = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


__all__ = ["HookCursorError", "decode_hook_cursor", "encode_hook_cursor"]
