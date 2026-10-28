"""Workspace usage reads: bounded windows, model consumption and Run duration."""

from datetime import UTC, date, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from a13n_service.infra.http import PageLimit
from a13n_service.infra.ids import ObjectId


class UsageWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: AwareDatetime
    end: AwareDatetime
    principal_id: ObjectId | None = Field(
        default=None, description="Filter by Run execution identity; requires workspace admin."
    )

    @field_validator("start", "end")
    @classmethod
    def utc(cls, value: AwareDatetime) -> AwareDatetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def bounded(self) -> "UsageWindow":
        if not timedelta(0) < self.end - self.start <= timedelta(days=366):
            raise ValueError("Usage windows must be positive and at most 366 days")
        return self


class OverviewQuery(UsageWindow):
    timezone: str = Field(default="UTC", max_length=128)

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use a valid IANA timezone") from None
        return value


class BreakdownQuery(UsageWindow):
    limit: PageLimit = 50
    cursor: str | None = Field(default=None, max_length=2048)


class ModelMetrics(BaseModel):
    requests: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_hit_rate: float | None
    # USD: zero for no requests, null when all requests are unpriced, otherwise the known subtotal.
    cost: Decimal | None
    unpriced_requests: int


class RunMetrics(BaseModel):
    runs: int
    average_duration_seconds: float | None


class DailyUsage(BaseModel):
    date: date
    usage: ModelMetrics


class UsageOverview(BaseModel):
    usage: ModelMetrics
    runs: RunMetrics
    daily: list[DailyUsage]


class AgentUsage(BaseModel):
    agent_id: str
    name: str
    usage: ModelMetrics
    runs: RunMetrics


class ModelUsageGroup(BaseModel):
    model: str | None
    name: str | None
    usage: ModelMetrics


class AgentUsagePage(BaseModel):
    items: list[AgentUsage]
    next_cursor: str | None


class MemberUsage(BaseModel):
    principal_id: str
    name: str
    kind: Literal["user", "service_account"]
    status: Literal["active", "disabled"]
    usage: ModelMetrics
    runs: RunMetrics


class MemberUsagePage(BaseModel):
    items: list[MemberUsage]
    next_cursor: str | None


class ModelUsagePage(BaseModel):
    items: list[ModelUsageGroup]
    next_cursor: str | None


class UsageFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: ObjectId | None = None
    thread_id: ObjectId | None = None
    session_id: ObjectId | None = None
    ingested_after: AwareDatetime | None = None
    ingested_before: AwareDatetime | None = None


class ModelUsage(BaseModel):
    # The model's key; None for records no model priced.
    model: str | None
    requests: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    # The sum of the costs priced at dispatch; None when no record of the model was priced.
    cost: Decimal | None
    # Mean first-chunk wait and output per full request second have independent eligibility.
    # Missing eligible observations remain None.
    average_time_to_first_chunk_seconds: float | None
    output_tokens_per_second: float | None


class RunUsage(BaseModel):
    run_id: str
    thread_id: str
    run_number: int = Field(ge=1, description="One-based chronological position among all Runs in the Thread.")
    models: list[ModelUsage]


class UsageSummary(BaseModel):
    models: list[ModelUsage]


class RunUsageQuery(UsageFilter):
    limit: PageLimit = 50
    cursor: str | None = Field(default=None, max_length=2048)


class RunUsagePage(BaseModel):
    items: list[RunUsage]
    next_cursor: str | None


class ModelRequestMetrics(BaseModel):
    """Derived from the current model contribution."""

    time_to_first_chunk_seconds: float | None
    request_duration_seconds: float | None
    output_tokens_per_second: float | None


class ContextUsageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thread_id: ObjectId | None = None
    run_id: ObjectId | None = None
    record_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def one_scope(self) -> "ContextUsageQuery":
        if sum(value is not None for value in (self.thread_id, self.run_id, self.record_id)) != 1:
            raise ValueError("Provide exactly one thread_id, run_id or record_id")
        return self


class ContextUsage(BaseModel):
    """A Run's configured window and its latest eligible generation, when available."""

    run_id: str
    record_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    context_window_tokens: int | None
