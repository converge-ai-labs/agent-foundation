"""Unverified JWT payload inspection for tokens already trusted by their transport."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime


def jwt_payload(token: str) -> dict[str, object] | None:
    try:
        segment = token.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except (IndexError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _jwtexpiry(token: str) -> datetime | None:
    payload = jwt_payload(token)
    value = None if payload is None else payload.get("exp")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
