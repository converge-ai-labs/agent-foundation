"""Channel tasks with exactly one calendar or event trigger."""

from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from a13n_service.connectivity.subscriptions import EventTrigger


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    timezone: str = Field(min_length=1, max_length=64, description="IANA timezone confirmed with the requester.")
    at: AwareDatetime | None = Field(
        default=None, description="One-time execution only. Omit for daily or weekly tasks."
    )
    time_of_day: str | None = Field(
        default=None,
        pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$",
        description="Recurring local clock time. For daily tasks set HH:MM and all weekdays; omit at.",
    )
    weekdays: tuple[int, ...] = Field(default=(0, 1, 2, 3, 4, 5, 6), min_length=1, max_length=7)

    @model_validator(mode="after")
    def validate_schedule(self):
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Unknown IANA timezone") from error
        if (self.at is None) == (self.time_of_day is None):
            raise ValueError("Choose one absolute at time OR a recurring local time_of_day")
        if len(set(self.weekdays)) != len(self.weekdays) or any(day < 0 or day > 6 for day in self.weekdays):
            raise ValueError("weekdays must be unique values from 0 (Monday) to 6 (Sunday)")
        return self

    def next_after(self, now: datetime) -> datetime | None:
        if self.at is not None:
            return self.at.astimezone(UTC) if self.at > now else None
        zone = ZoneInfo(self.timezone)
        assert self.time_of_day is not None
        clock = time.fromisoformat(self.time_of_day)
        start = now.astimezone(zone).date()
        for offset in range(15):
            date = start + timedelta(days=offset)
            if date.weekday() not in self.weekdays:
                continue
            # Skip nonexistent local times; on a repeated clock hour use its first occurrence only.
            local = datetime.combine(date, clock, tzinfo=zone)
            candidate = local.astimezone(UTC)
            if candidate.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
                continue
            if candidate > now:
                return candidate
        raise ValueError("No occurrence in the next two weeks")

    def describe(self) -> str:
        if self.at is not None:
            return f"Once: {self.at.astimezone(ZoneInfo(self.timezone)):%Y-%m-%d %H:%M} ({self.timezone})"
        if set(self.weekdays) == set(range(7)):
            return f"Every day at {self.time_of_day} ({self.timezone})"
        if set(self.weekdays) == set(range(5)):
            return f"Weekdays at {self.time_of_day} ({self.timezone})"
        days = ", ".join(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[d] for d in sorted(self.weekdays))
        return f"{days} at {self.time_of_day} ({self.timezone})"


class RoutineDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    title: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=4000, description="Self-contained instructions for each future run.")
    schedule: Schedule | None = None
    event: EventTrigger | None = None

    @model_validator(mode="after")
    def one_trigger(self):
        if (self.schedule is None) == (self.event is None):
            raise ValueError("Choose exactly one schedule or event trigger")
        return self


class ProposeRoutine(BaseModel):
    """Propose a task or change; the requester must confirm its confirmation card before it takes effect."""

    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=1, max_length=64, description="Reuse for retries of this proposal.")
    routine_id: str | None = Field(default=None, max_length=72)
    operation: Literal["save", "pause", "resume", "delete"] = "save"
    definition: RoutineDefinition | None = None

    @model_validator(mode="after")
    def valid_operation(self):
        if (self.operation == "save") != (self.definition is not None):
            raise ValueError("save requires a definition; other operations must omit it")
        if self.operation != "save" and self.routine_id is None:
            raise ValueError("A routine_id is required")
        return self


class ListRoutines(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cursor: str | None = Field(default=None, max_length=72)
