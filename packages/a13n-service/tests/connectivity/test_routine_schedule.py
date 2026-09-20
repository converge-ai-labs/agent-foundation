from datetime import UTC, datetime

import pytest
from a13n_service.bots.routines.domain import Schedule
from pydantic import ValidationError


def test_weekday_rollover_and_timezone():
    schedule = Schedule(timezone="Asia/Shanghai", time_of_day="09:00", weekdays=(0, 1, 2, 3, 4))
    assert schedule.next_after(datetime(2026, 9, 18, 1, 0, tzinfo=UTC)) == datetime(2026, 9, 21, 1, 0, tzinfo=UTC)


def test_once_is_not_repeated():
    at = datetime(2026, 9, 21, 1, 0, tzinfo=UTC)
    schedule = Schedule(timezone="Asia/Shanghai", at=at)
    assert schedule.next_after(datetime(2026, 9, 20, tzinfo=UTC)) == at
    assert schedule.next_after(at) is None


def test_dst_nonexistent_time_is_skipped():
    schedule = Schedule(timezone="America/New_York", time_of_day="02:30")
    assert schedule.next_after(datetime(2026, 3, 8, 0, tzinfo=UTC)) == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)


def test_dst_repeated_time_runs_only_once():
    schedule = Schedule(timezone="America/New_York", time_of_day="01:30")
    assert schedule.next_after(datetime(2026, 11, 1, 0, tzinfo=UTC)) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert schedule.next_after(datetime(2026, 11, 1, 5, 30, tzinfo=UTC)) == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "changes",
    [
        {"timezone": "Unknown/Nowhere"},
        {"time_of_day": "24:00"},
        {"weekdays": (7,)},
        {"weekdays": (0, 0)},
        {"weekdays": ()},
        {"time_of_day": None},
        {"at": "2026-10-01T09:00:00"},
        {"at": "2026-10-01T09:00:00+08:00"},
    ],
)
def test_invalid_schedules_fail_closed(changes):
    with pytest.raises(ValidationError):
        Schedule.model_validate({"timezone": "Asia/Shanghai", "time_of_day": "09:00", **changes})
