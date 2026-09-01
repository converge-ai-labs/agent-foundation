"""Typed public and provider-side trace query values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

TraceStatus = Literal["unset", "ok", "error"]
ObservationType = Literal["span", "generation", "event", "unknown"]
AttemptOutcome = Literal["succeeded", "yielded", "failed", "cancelled"]


class SearchIn(StrEnum):
    input = "input"
    output = "output"
    input_output = "input_output"


class TraceView(StrEnum):
    compact = "compact"
    full = "full"


class TraceSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int | None
    trace_status: TraceStatus
    session_id: str
    thread_id: str
    run_id: str
    run_attempt_id: str
    run_attempt_number: int
    run_attempt_outcome: AttemptOutcome | None
    input: JsonValue | None
    output: JsonValue | None
    observation_count: int | None
    models: tuple[str, ...]
    usage: Mapping[str, int] | None
    total_cost_usd: Decimal | None
    source_url: str | None


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    parent_id: str | None
    type: ObservationType
    name: str
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int | None
    status: TraceStatus
    model: str | None
    usage: Mapping[str, int] | None
    cost_usd: Decimal | None
    input: JsonValue | None
    output: JsonValue | None
    metadata: Mapping[str, JsonValue]


class TraceDetail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trace: TraceSummary
    observations: tuple[Observation, ...]


class TraceCollection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[TraceSummary, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class TraceQueryCapabilities:
    input_search: bool = False
    output_search: bool = False
    combined_input_output_search: bool = False
    usage: bool = False
    cost: bool = False
    source_url: bool = False


@dataclass(frozen=True, slots=True)
class TraceCorrelation:
    organization_id: str
    workspace_id: str
    session_id: str
    thread_id: str
    run_id: str
    run_attempt_id: str
    agent_preset_id: str


@dataclass(frozen=True, slots=True)
class ProviderTraceQuery:
    organization_id: str
    workspace_id: str
    from_started_at: datetime
    to_started_at: datetime
    limit: int
    cursor: str | None = None
    query: str | None = None
    search_in: SearchIn | None = None
    thread_id: str | None = None
    run_id: str | None = None
    run_attempt_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderTraceSummary:
    id: str
    name: str
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int | None
    trace_status: TraceStatus
    correlation: TraceCorrelation
    input: JsonValue | None
    output: JsonValue | None
    observation_count: int | None
    models: tuple[str, ...]
    usage: Mapping[str, int] | None
    total_cost_usd: Decimal | None
    source_url: str | None


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    id: str
    parent_id: str | None
    type: ObservationType
    name: str
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int | None
    status: TraceStatus
    model: str | None
    usage: Mapping[str, int] | None
    cost_usd: Decimal | None
    input: JsonValue | None
    output: JsonValue | None
    metadata: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ProviderTracePage:
    items: tuple[ProviderTraceSummary, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ProviderTraceDetail:
    trace: ProviderTraceSummary
    observations: tuple[ProviderObservation, ...]
