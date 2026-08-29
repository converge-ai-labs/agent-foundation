"""Shared Foundation object-ID generation and validation."""

from __future__ import annotations

import re
import secrets

_ID_PATTERN = re.compile(r"^(?P<prefix>[a-z][a-z0-9]{1,11})_(?P<suffix>[a-z0-9]{20,64})$")


def new_object_id(prefix: str) -> str:
    """Generate one unpredictable lowercase kind-prefixed object ID."""

    if not re.fullmatch(r"[a-z][a-z0-9]{1,11}", prefix):
        raise ValueError("object ID prefix must be 2-12 lowercase ASCII letters or digits")
    return f"{prefix}_{secrets.token_hex(16)}"


def validate_object_id(value: str, *, prefix: str | None = None) -> str:
    """Validate one Foundation object ID and optionally require its kind."""

    match = _ID_PATTERN.fullmatch(value)
    if match is None or (prefix is not None and match.group("prefix") != prefix):
        raise ValueError("invalid Foundation object ID")
    return value
