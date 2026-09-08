"""Canonical wall-clock and UTC normalization helpers."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator

type Clock = Callable[[], datetime]


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""

    return datetime.now(UTC)


def assume_utc(value: datetime) -> datetime:
    """Normalize a datetime, treating database-naive values as UTC."""

    if value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def require_aware_utc(value: datetime) -> datetime:
    """Normalize an aware datetime and reject a naive protocol value."""

    if value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def optional_assume_utc(value: datetime | None) -> datetime | None:
    """Normalize an optional datetime using the database-naive UTC policy."""

    return None if value is None else assume_utc(value)


UtcDateTime = Annotated[datetime, AfterValidator(require_aware_utc)]

__all__ = ["Clock", "UtcDateTime", "assume_utc", "optional_assume_utc", "require_aware_utc", "utc_now"]
