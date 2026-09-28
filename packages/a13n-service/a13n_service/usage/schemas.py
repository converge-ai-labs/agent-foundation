"""Workspace usage reads: bounded windows, model consumption and Run duration."""

from datetime import UTC, date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from a13n_service.infra.http import PageLimit
from a13n_service.infra.ids import ObjectId


class UsageWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: AwareDatetime
    end: AwareDatetime

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


class UsageSummary(BaseModel):
    models: list[ModelUsage]
