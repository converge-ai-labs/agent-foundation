"""Calendar boundaries are deterministic and require neither a running clock nor stores."""

from datetime import UTC, datetime

import pytest
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.schedules.calendar import Calendar


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


@pytest.mark.parametrize(
    ("expression", "after", "expected"),
    [
        ("0 9 * * *", "2026-10-19T01:00:00Z", "2026-10-20T01:00:00Z"),
        ("30 9 * * 1-5", "2026-10-23T01:30:00Z", "2026-10-26T01:30:00Z"),
        ("*/15 * * * *", "2026-10-19T01:01:00Z", "2026-10-19T01:15:00Z"),
        ("0 9 1 * *", "2026-10-19T01:00:00Z", "2026-11-01T01:00:00Z"),
        ("0 9 1 * 1", "2026-10-19T01:00:00Z", "2026-10-26T01:00:00Z"),
        ("0 9 1 * 1", "2026-10-31T01:00:00Z", "2026-11-01T01:00:00Z"),
        ("0 9 29 2 *", "2025-03-01T00:00:00Z", "2028-02-29T01:00:00Z"),
        ("0 9 * * 7", "2026-10-19T00:00:00Z", "2026-10-25T01:00:00Z"),
        ("5,20-30/5 9 * * *", "2026-10-19T01:20:00Z", "2026-10-19T01:25:00Z"),
    ],
)
def test_next_local_time(expression: str, after: str, expected: str) -> None:
    calendar = Calendar.parse(expression, "Asia/Shanghai")
    assert calendar.next(instant(after)) == instant(expected)


def test_dst_skips_nonexistent_time() -> None:
    calendar = Calendar.parse("30 2 * * *", "America/New_York")
    assert calendar.next(instant("2026-03-07T07:30:00Z")) == instant("2026-03-09T06:30:00Z")


def test_dst_repeated_time_has_two_distinct_occurrences() -> None:
    calendar = Calendar.parse("30 1 * * *", "America/New_York")
    first = calendar.next(instant("2026-11-01T00:00:00Z"))
    second = calendar.next(first)
    assert first == instant("2026-11-01T05:30:00Z")
    assert second == instant("2026-11-01T06:30:00Z")
    assert calendar.previous(second) == first
    assert calendar.previous(instant("2026-11-01T06:31:00Z")) == second


def test_recovery_finds_latest_time_without_enumerating_missed_minutes() -> None:
    calendar = Calendar.parse("* * * * *", "UTC")
    assert calendar.previous(instant("2026-10-19T09:03:10Z")) == instant("2026-10-19T09:03:00Z")
    assert calendar.previous(instant("2026-10-19T09:03:00Z")) == instant("2026-10-19T09:02:00Z")


@pytest.mark.parametrize(
    "expression",
    [
        "* * * *",
        "* * * * * *",
        "@daily",
        "60 * * * *",
        "*/0 * * * *",
        "3-1 * * * *",
        "0 0 L * *",
        "0 0 * * MON",
        "0 0 * * 8",
        "0,,1 * * * *",
    ],
)
def test_rejects_unsupported_cron(expression: str) -> None:
    with pytest.raises(ServiceError) as error:
        Calendar.parse(expression, "UTC")
    assert error.value.details["field"] == "schedule.expression"


def test_impossible_date_and_invalid_timezone_are_explicit_errors() -> None:
    with pytest.raises(ServiceError, match="no_match_within_eight_years"):
        Calendar.parse("0 9 30 2 *", "UTC").next(datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ServiceError, match="unknown_timezone"):
        Calendar.parse("0 9 * * *", "Not/AZone")
    with pytest.raises(ServiceError, match="timezone_required"):
        Calendar.parse("0 9 * * *", "UTC").next(datetime(2026, 1, 1))


@pytest.mark.parametrize("instant", [datetime.min.replace(tzinfo=UTC), datetime.max.replace(tzinfo=UTC)])
def test_calendar_range_overflow_is_validation_error(instant: datetime) -> None:
    with pytest.raises(ServiceError, match="no_match_within_eight_years"):
        Calendar.parse("0 9 30 2 *", "UTC").next(instant)
