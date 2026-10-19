"""Five-field numeric cron with explicit local-time and bounded calendar semantics.

Search dates, not elapsed minutes. A matching local minute is round-tripped through UTC:
nonexistent minutes disappear and both occurrences of an ambiguous minute remain.
"""

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from importlib.resources import files
from typing import Self
from zoneinfo import ZoneInfo

from croniter import CroniterBadCronError, CroniterBadDateError, croniter
from tzdata import IANA_VERSION

from a13n_service.infra.errors import invalid

_PART = re.compile(r"(\*|[0-9]+(?:-[0-9]+)?)(?:/([0-9]+))?\Z")
TIMEZONE_DATA_VERSION = IANA_VERSION


@lru_cache(maxsize=128)
def zone(name: str) -> ZoneInfo:
    """All replicas use the locked tzdata release, independent of their host's zone database."""
    if not re.fullmatch(r"[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*", name):
        raise invalid("schedule.timezone", "unknown_timezone")
    try:
        with files("tzdata.zoneinfo").joinpath(*name.split("/")).open("rb") as source:
            return ZoneInfo.from_file(source, key=name)
    except (FileNotFoundError, IsADirectoryError, ValueError):
        raise invalid("schedule.timezone", "unknown_timezone") from None


@dataclass(frozen=True, slots=True)
class Calendar:
    expression: str
    timezone: ZoneInfo
    minutes: tuple[int, ...]
    hours: tuple[int, ...]

    @classmethod
    def parse(cls, expression: str, timezone: str) -> Self:
        fields = expression.split()
        if len(fields) != 5 or len(expression) > 256:
            raise invalid("schedule.expression", "expected_five_cron_fields")
        # Croniter supports names, seconds and other extensions; this API intentionally accepts numeric POSIX fields.
        for field in fields:
            for part in field.split(","):
                match = _PART.fullmatch(part)
                if match is None:
                    raise invalid("schedule.expression", "invalid_cron_field")
                span = match[1]
                if "-" in span and int(span.split("-")[0]) > int(span.split("-")[1]):
                    raise invalid("schedule.expression", "invalid_cron_range")
        try:
            expanded, _ = croniter.expand(" ".join(fields))
        except CroniterBadCronError:
            raise invalid("schedule.expression", "invalid_cron_field") from None
        minutes = tuple(range(60)) if expanded[0] == ["*"] else tuple(int(value) for value in expanded[0])
        hours = tuple(range(24)) if expanded[1] == ["*"] else tuple(int(value) for value in expanded[1])
        return cls(" ".join(fields), zone(timezone), minutes, hours)

    def _times(self, day: date) -> list[datetime]:
        values: set[datetime] = set()
        for hour in self.hours:
            for minute in self.minutes:
                local = datetime(day.year, day.month, day.day, hour, minute)
                for fold in (0, 1):
                    utc = local.replace(tzinfo=self.timezone, fold=fold).astimezone(UTC)
                    if utc.astimezone(self.timezone).replace(tzinfo=None) == local:
                        values.add(utc)
        return sorted(values)

    def _find(self, instant: datetime, *, forward: bool) -> datetime:
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise invalid("after", "timezone_required")
        try:
            instant = instant.astimezone(UTC)
            day = instant.astimezone(self.timezone).date()
            # Ask croniter for matching calendar dates, then apply our explicit timezone rules to their minutes.
            # This skips years without a matching date rather than traversing every minute of a backlog.
            date_expression = "0 0 " + " ".join(self.expression.split()[2:])
            boundary = datetime.combine(day, time.min if forward else time.max)
            if forward:
                boundary -= timedelta(microseconds=1)
            dates = croniter(date_expression, boundary, max_years_between_matches=8, day_or=True)
            while True:
                local = dates.get_next(datetime) if forward else dates.get_prev(datetime)
                if abs(local.year - day.year) > 8:
                    break
                times = self._times(local.date())
                index = bisect_right(times, instant) if forward else bisect_left(times, instant) - 1
                if 0 <= index < len(times):
                    return times[index]
        except (CroniterBadDateError, OverflowError, ValueError):
            pass
        raise invalid("schedule.expression", "no_match_within_eight_years")

    def next(self, after: datetime) -> datetime:
        """First matching UTC minute strictly after the supplied instant."""
        return self._find(after, forward=True)

    def previous(self, before: datetime) -> datetime:
        """Last matching UTC minute strictly before the supplied instant."""
        return self._find(before, forward=False)
