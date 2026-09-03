"""Opaque, query-bound cursor encoding for Model Management collections."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime

from a13n_service.temporal import assume_utc


class CursorError(ValueError):
    pass


def encode_model_cursor(*, updated_at: datetime, model_id: str, scope: dict[str, object]) -> str:
    payload = {
        "v": "1",
        "updated_at": assume_utc(updated_at).isoformat().replace("+00:00", "Z"),
        "id": model_id,
        "scope": _scope_digest(scope),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()


def decode_model_cursor(value: str, *, scope: dict[str, object]) -> tuple[datetime, str]:
    if not value or len(value) > 2048:
        raise CursorError("invalid cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if not isinstance(payload, dict) or payload.get("v") != "1" or payload.get("scope") != _scope_digest(scope):
            raise CursorError("cursor does not match this query")
        updated_at = datetime.fromisoformat(str(payload["updated_at"]).replace("Z", "+00:00"))
        model_id = payload["id"]
        if not isinstance(model_id, str) or not model_id.startswith("mdl_"):
            raise CursorError("invalid cursor")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        if isinstance(error, CursorError):
            raise
        raise CursorError("invalid cursor") from error
    return assume_utc(updated_at), model_id


def _scope_digest(scope: dict[str, object]) -> str:
    encoded = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()
