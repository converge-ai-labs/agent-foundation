"""Opaque, Principal-bound Workspace lifecycle cursor."""

from __future__ import annotations

import base64
import hashlib
import json


class LifecycleCursorError(ValueError):
    pass


def encode_lifecycle_cursor(*, sequence: int, scope: dict[str, object]) -> str:
    if sequence < 0:
        raise LifecycleCursorError("cursor sequence must be non-negative")
    payload = {"v": "1", "seq": sequence, "scope": _scope_digest(scope)}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()


def decode_lifecycle_cursor(value: str, *, scope: dict[str, object]) -> int:
    if not value or len(value) > 2048:
        raise LifecycleCursorError("invalid cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if not isinstance(payload, dict) or payload.get("v") != "1" or payload.get("scope") != _scope_digest(scope):
            raise LifecycleCursorError("cursor does not match this collection")
        sequence = payload["seq"]
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise LifecycleCursorError("invalid cursor sequence")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        if isinstance(error, LifecycleCursorError):
            raise
        raise LifecycleCursorError("invalid cursor") from error
    return sequence


def _scope_digest(scope: dict[str, object]) -> str:
    encoded = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["LifecycleCursorError", "decode_lifecycle_cursor", "encode_lifecycle_cursor"]
