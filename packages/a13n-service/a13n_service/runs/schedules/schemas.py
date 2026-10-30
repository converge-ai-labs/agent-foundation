"""Schedule configuration, immutable trigger intent and compact history projections."""

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from a13n_service.infra.http import PageLimit
from a13n_service.infra.ids import ObjectId
from a13n_service.infra.labels import Labels
from a13n_service.resources.memories.schemas import MemoryMounts
from a13n_service.runs.schedules.calendar import Calendar
from a13n_service.runs.schemas import (
    EntryStatus,
    JsonPart,
    MessagePayload,
    RunOptions,
    RunStatus,
    TextPart,
    canonical_json,
)

type ScheduleState = Literal["paused", "enabled", "completed", "archived"]
type OccurrenceStatus = Literal["pending", "submitted", "completed", "skipped", "failed", "cancelled"]
type ScheduleTrigger = Literal["scheduled", "manual"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class CronSchedule(_Model):
    type: Literal["cron"] = "cron"
    expression: str = Field(min_length=1, max_length=256)
    timezone: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def valid(self) -> Self:
        Calendar.parse(self.expression, self.timezone)
        return self


class OnceSchedule(_Model):
    type: Literal["once"]
    run_at: AwareDatetime

    @field_validator("run_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


# Keep cron's omitted type compatible with existing requests and stored snapshots.
type ScheduleRule = CronSchedule | OnceSchedule


class ScheduleInvocation(_Model):
    agent_id: ObjectId
    agent_revision_id: ObjectId | None
    payload: MessagePayload
    options: RunOptions = Field(default_factory=RunOptions)
    memories: MemoryMounts = ()

    @field_validator("payload")
    @classmethod
    def durable_content(cls, value: MessagePayload) -> MessagePayload:
        if any(not isinstance(part, TextPart | JsonPart) for part in value.content):
            raise ValueError("Schedules accept only text and JSON content")
        return value


class ScheduleTarget(_Model):
    """Omitted IDs create a session and, for thread mode, one empty reusable thread."""

    mode: Literal["new_thread", "thread"] = "new_thread"
    session_id: ObjectId | None = None
    thread_id: ObjectId | None = None

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.mode == "new_thread" and self.thread_id is not None:
            raise ValueError("new_thread targets cannot specify thread_id")
        return self


class ScheduleConfig(_Model):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2048)
    labels: Labels = Field(default_factory=dict)
    schedule: ScheduleRule
    max_lateness_seconds: int = Field(default=300, ge=60, le=86400)
    invocation: ScheduleInvocation

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len(canonical_json(self.model_dump(mode="json", include=set(ScheduleConfig.model_fields)))) > 262144:
            raise ValueError("Schedule configuration exceeds 256 KiB")
        return self


class ScheduleCreate(ScheduleConfig):
    target: ScheduleTarget = Field(default_factory=ScheduleTarget, exclude_if=lambda value: value == ScheduleTarget())
    state: Literal["paused", "enabled"] = "paused"


class ScheduleUpdate(_Model):
    target: ScheduleTarget | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2048)
    labels: Labels | None = None
    schedule: ScheduleRule | None = None
    max_lateness_seconds: int | None = Field(default=None, ge=60, le=86400)
    invocation: ScheduleInvocation | None = None

    @model_validator(mode="after")
    def explicit_null(self) -> Self:
        if any(getattr(self, name) is None for name in self.model_fields_set - {"description"}):
            raise ValueError("Only description can be cleared with null")
        return self


class Schedule(ScheduleConfig):
    target: ScheduleTarget
    id: str
    session_id: str
    stream_url: str
    workspace_id: str
    principal_id: str
    created_by_id: str
    updated_by_id: str
    state: ScheduleState
    pause_reason: Literal["manual", "authority_invalid", "target_invalid"] | None
    version: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class SchedulePage(_Model):
    items: list[Schedule]
    next_cursor: str | None = None


class ScheduleQuery(_Model):
    limit: PageLimit = 50
    cursor: str | None = None
    state: ScheduleState | None = None
    agent_id: ObjectId | None = None
    principal_id: ObjectId | None = None
    label: list[str] = Field(default_factory=list, max_length=20)
    q: str | None = Field(default=None, max_length=128)


class OccurrenceSnapshot(_Model):
    name: str
    schedule: ScheduleRule
    max_lateness_seconds: int
    invocation: ScheduleInvocation
    timezone_data_version: str
    utc_offset_seconds: int | None


class InitialRun(_Model):
    id: str
    status: RunStatus


class OccurrenceExecution(_Model):
    """Current execution observations alongside the durable occurrence completion facts."""

    initial_entry_status: EntryStatus | None
    initial_run: InitialRun | None
    has_unfinished_work: bool
    observed_at: datetime


class OccurrenceSummary(_Model):
    id: str
    schedule_id: str
    session_id: str
    trigger: ScheduleTrigger
    scheduled_at: datetime | None
    covered_from_at: datetime | None
    schedule_version: int
    status: OccurrenceStatus
    reason: str | None
    thread_id: str | None
    entry_id: str | None
    dispatch_attempts: int
    next_attempt_at: datetime | None
    deadline_at: datetime
    last_error: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    completed_at: datetime | None = None
    completion_run_id: str | None = None
    execution_outcome: Literal["succeeded", "failed", "cancelled"] | None = None
    execution: OccurrenceExecution | None = Field(default=None, exclude_if=lambda value: value is None)


class Occurrence(OccurrenceSummary):
    snapshot: OccurrenceSnapshot
    principal_id: str
    requested_by_id: str | None


class OccurrencePage(_Model):
    items: list[OccurrenceSummary]
    next_cursor: str | None = None


class OccurrenceQuery(_Model):
    limit: PageLimit = 50
    cursor: str | None = None
    status: OccurrenceStatus | None = None
    trigger: ScheduleTrigger | None = None
    created_after: AwareDatetime | None = None
    created_before: AwareDatetime | None = None
    unfinished: bool | None = None
    include: Literal["execution"] | None = None


class BusyOccurrence(_Model):
    occurrence_id: str
    root_thread_id: str
    entry_id: str


class BusyOccurrencePage(_Model):
    items: list[BusyOccurrence]
    next_cursor: str | None = None


class ScheduleStatus(_Model):
    schedule_id: str
    session_id: str
    observed_at: datetime
    next_fire_at: datetime | None
    pending_occurrence_id: str | None
    last_occurrence_id: str | None
    has_unfinished_work: bool
    busy_occurrences: BusyOccurrencePage


class SchedulePreviewRequest(_Model):
    schedule: ScheduleRule
    after: AwareDatetime
    count: int = Field(default=5, ge=1, le=10)


class ScheduleTime(_Model):
    utc: datetime
    local: datetime


class SchedulePreview(_Model):
    items: list[ScheduleTime]


class ScheduleCommand(_Model):
    """Commands accept no configuration or authority overrides."""
