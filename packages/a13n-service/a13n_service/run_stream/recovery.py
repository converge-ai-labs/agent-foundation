"""The fixed terminal display recovery horizon, independent of raw replay TTL."""

from datetime import datetime, timedelta

from a13n_service.temporal import assume_utc

DISPLAY_RECOVERY_WINDOW = timedelta(days=1)


def display_recovery_deadline(sealed_at: datetime | None) -> datetime | None:
    return None if sealed_at is None else assume_utc(sealed_at) + DISPLAY_RECOVERY_WINDOW
