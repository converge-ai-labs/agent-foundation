"""Opaque query-bound cursor for Asset collections."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime

from a13n_service.temporal import assume_utc


class AssetCursorError(ValueError):
    pass


def encode_asset_cursor(*, created_at: datetime, asset_id: str, scope: dict[str, object]) -> str:
    payload = {
        "v": "1",
        "created_at": assume_utc(created_at).isoformat().replace("+00:00", "Z"),
        "id": asset_id,
        "scope": _scope_digest(scope),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()


def decode_asset_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    if not value or len(value) > 2048:
        raise AssetCursorError("invalid cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if not isinstance(payload, dict) or payload.get("v") != "1" or payload.get("scope") != _scope_digest(scope):
            raise AssetCursorError("cursor does not match this query")
        created_at = datetime.fromisoformat(str(payload["created_at"]).replace("Z", "+00:00"))
        asset_id = payload["id"]
        if not isinstance(asset_id, str) or not asset_id.startswith("ast_"):
            raise AssetCursorError("invalid cursor")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        if isinstance(error, AssetCursorError):
            raise
        raise AssetCursorError("invalid cursor") from error
    return assume_utc(created_at), asset_id


def _scope_digest(scope: dict[str, object]) -> str:
    encoded = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()
