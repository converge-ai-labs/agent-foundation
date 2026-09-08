"""Strong representation tags for mutable resource heads."""

from __future__ import annotations

import hashlib
from datetime import datetime

from a13n_service.temporal import assume_utc


def resource_etag(resource_id: str, updated_at: datetime) -> str:
    """Return a strong ETag for one committed resource-head representation."""

    normalized = assume_utc(updated_at)
    evidence = f"{resource_id}\0{normalized.isoformat(timespec='microseconds')}".encode()
    return f'"{hashlib.sha256(evidence).hexdigest()}"'


def etag_matches(if_match: str, current: str) -> bool:
    """Match one exact strong tag; wildcard and weak validators are not accepted."""

    return if_match.strip() == current
