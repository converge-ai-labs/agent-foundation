"""Strong representation tags for mutable resource heads."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime


def resource_etag(resource_id: str, updated_at: datetime) -> str:
    """Return a strong ETag for one committed resource-head representation."""

    normalized = updated_at.replace(tzinfo=UTC) if updated_at.tzinfo is None else updated_at.astimezone(UTC)
    evidence = f"{resource_id}\0{normalized.isoformat(timespec='microseconds')}".encode()
    return f'"{hashlib.sha256(evidence).hexdigest()}"'


def etag_matches(if_match: str, current: str) -> bool:
    """Match one exact strong tag; wildcard and weak validators are not accepted."""

    return if_match.strip() == current
