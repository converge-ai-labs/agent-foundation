from datetime import UTC, datetime, timedelta, timezone

import pytest
from a13n_service.temporal import assume_utc, optional_assume_utc, require_aware_utc, utc_now


def test_utc_now_is_timezone_aware() -> None:
    assert utc_now().tzinfo is UTC


def test_assume_utc_preserves_instant_for_aware_values() -> None:
    source = datetime(2026, 9, 4, 8, 30, tzinfo=timezone(timedelta(hours=8)))

    assert assume_utc(source) == datetime(2026, 9, 4, 0, 30, tzinfo=UTC)


def test_assume_utc_treats_database_naive_values_as_utc() -> None:
    source = datetime(2026, 9, 4, 0, 30)

    assert assume_utc(source) == datetime(2026, 9, 4, 0, 30, tzinfo=UTC)
    assert optional_assume_utc(None) is None


def test_require_aware_utc_rejects_naive_values() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        require_aware_utc(datetime(2026, 9, 4, 0, 30))
