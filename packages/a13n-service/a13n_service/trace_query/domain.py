"""Shared normalized trace resources and scoped provider queries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

TraceStatus = Literal["unset", "ok", "error"]


class SearchIn(StrEnum):
    input = "input"
    output = "output"
    input_output = "input_output"


class TraceView(StrEnum):
    compact = "compact"
    full = "full"


class TraceValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class Content(TraceValue):
    media_type: str | None
    value: JsonValue


class ModelIdentity(TraceValue):
    requested: str | None
    response: str | None


class InstrumentationScope(TraceValue):
    name: str | None
    version: str | None
    attributes: Mapping[str, JsonValue] | None


class ObservationEvent(TraceValue):
    name: str
    occurred_at: datetime
    attributes: Mapping[str, JsonValue]


class ObservationLink(TraceValue):
    trace_id: str
    observation_id: str
    attributes: Mapping[str, JsonValue] | None


class Observation(TraceValue):
    id: str
    parent_id: str | None
    type: str
    name: str
    started_at: datetime
    ended_at: datetime | None
    status: TraceStatus | None
    level: str | None
    status_message: str | None
    model: ModelIdentity | None
    usage: Mapping[str, int] | None
    cost_usd: Decimal | None
    input: Content | None
    output: Content | None
    attributes: Mapping[str, JsonValue] | None
    resource_attributes: Mapping[str, JsonValue] | None
    scope: InstrumentationScope | None
    events: tuple[ObservationEvent, ...] | None
    links: tuple[ObservationLink, ...] | None


class TraceCorrelation(TraceValue):
    organization_id: str
    workspace_id: str
    session_id: str
    thread_id: str
    run_id: str
    run_attempt_id: str
    agent_id: str


class Trace(TraceValue):
    id: str
    provider: str
    correlation: TraceCorrelation
    root: Observation
    source_url: str | None


class TraceCollection(TraceValue):
    items: tuple[Trace, ...]
    next_cursor: str | None


class ObservationCollection(TraceValue):
    items: tuple[Observation, ...]
    next_cursor: str | None


class TraceQueryDescriptor(TraceValue):
    provider: str
    enabled: bool
    search_in: tuple[SearchIn, ...]
    history_from: datetime | None


@dataclass(frozen=True, slots=True)
class TraceQueryCapabilities:
    search_in: tuple[SearchIn, ...] = ()
    history_from: datetime | None = None


OBSERVATION_METADATA_PREFIX = "a13n.observation.metadata."


@dataclass(frozen=True, slots=True)
class ProviderTraceQuery:
    organization_id: str
    workspace_id: str
    from_started_at: datetime
    to_started_at: datetime
    limit: int
    view: TraceView = TraceView.compact
    cursor: str | None = None
    query: str | None = None
    search_in: SearchIn | None = None
    session_id: str | None = None
    thread_id: str | None = None
    run_id: str | None = None
    run_attempt_id: str | None = None
    # Observation-metadata key/value pairs matched on the root span.
    metadata: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderTraceRead:
    organization_id: str
    workspace_id: str
    trace_id: str
    history_from: datetime | None
    to_started_at: datetime
    view: TraceView
    limit: int = 50
    cursor: str | None = None


def project_observation(item: Observation, view: TraceView) -> Observation:
    """Apply the same content projection to roots and other observations."""
    if view is TraceView.full:
        return item
    return item.model_copy(
        update={
            "input": None,
            "output": None,
            "attributes": None,
            "resource_attributes": None,
            "scope": None,
            "status_message": None,
            "events": None,
            "links": (
                tuple(link.model_copy(update={"attributes": None}) for link in item.links)
                if item.links is not None
                else None
            ),
        }
    )


def project_trace(item: Trace, view: TraceView) -> Trace:
    return item.model_copy(update={"root": project_observation(item.root, view)})
