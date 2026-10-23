"""Provider-neutral trace metric query semantics, summaries and paging."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Self

from a13n_harness.observation import redact_json
from pydantic import BaseModel, JsonValue

from a13n_service.infra import cursors
from a13n_service.infra.errors import ServiceError
from a13n_service.providers.traces import Span, SpanQuery


class TraceSearchItem(BaseModel):
    root: Span
    duration_seconds: Decimal | None
    cost_usd: Decimal | None


class TraceSearchPage(BaseModel):
    items: list[TraceSearchItem]
    next_cursor: str | None
    total: int
    collected_at: datetime


@dataclass(frozen=True, slots=True)
class SearchQuery:
    roots: SpanQuery
    correlation: Mapping[str, str]
    order_by: Literal["started", "duration", "cost"]
    direction: Literal["asc", "desc"]
    min_duration_seconds: Decimal | None = None
    max_duration_seconds: Decimal | None = None
    min_cost_usd: Decimal | None = None
    max_cost_usd: Decimal | None = None
    cursor: str | None = None

    def page_owner(self, owner: str) -> str:
        return cursors.query_owner(
            owner,
            self.order_by,
            self.direction,
            self.min_duration_seconds,
            self.max_duration_seconds,
            self.min_cost_usd,
            self.max_cost_usd,
        )


@dataclass(frozen=True, slots=True)
class SearchPosition:
    """Last sort value and unique identity; unknown metrics always follow known metrics."""

    value: str | None
    trace_id: str
    span_id: str

    @classmethod
    def read(cls, values: list[JsonValue], query: SearchQuery) -> Self:
        try:
            if len(values) != 3:
                raise ValueError("position")
            value, trace_id, span_id = values
            if not isinstance(trace_id, str) or not re.fullmatch(r"[0-9a-f]{32}", trace_id):
                raise ValueError("trace")
            if not isinstance(span_id, str) or not re.fullmatch(r"[0-9a-f]{16}", span_id):
                raise ValueError("span")
            if value is not None and (not isinstance(value, str) or len(value) > 128):
                raise ValueError("value")
            if query.order_by == "started":
                if value is None or datetime.fromisoformat(value).tzinfo is None:
                    raise ValueError("time")
            elif value is not None and not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value):
                raise ValueError("metric")
            return cls(value, trace_id, span_id)
        except ValueError:
            raise ServiceError("invalid_cursor", "Invalid trace search cursor") from None

    @classmethod
    def from_item(cls, item: TraceSearchItem, query: SearchQuery) -> Self:
        metric = item.duration_seconds if query.order_by == "duration" else item.cost_usd
        value = (
            item.root.started_at.isoformat()
            if query.order_by == "started"
            else (format(metric, "f") if metric is not None else None)
        )
        return cls(value, item.root.trace_id, item.root.id)

    def values(self) -> list[JsonValue]:
        return [self.value, self.trace_id, self.span_id]

    def precedes(self, item: TraceSearchItem, query: SearchQuery) -> bool:
        """Whether an item belongs strictly after this position in the requested order."""
        identity_after = (item.root.trace_id, item.root.id) > (self.trace_id, self.span_id)
        if query.order_by == "started":
            assert self.value is not None
            boundary = datetime.fromisoformat(self.value)
            value = item.root.started_at
            return (
                identity_after
                if value == boundary
                else (value > boundary if query.direction == "asc" else value < boundary)
            )
        metric = item.duration_seconds if query.order_by == "duration" else item.cost_usd
        if self.value is None:
            return metric is None and identity_after
        if metric is None:
            return True
        boundary_metric = Decimal(self.value)
        return (
            identity_after
            if metric == boundary_metric
            else (metric > boundary_metric if query.direction == "asc" else metric < boundary_metric)
        )


def matches_root(span: Span, query: SpanQuery) -> bool:
    def text(value: object) -> str | None:
        return value if isinstance(value, str) else json.dumps(value) if isinstance(value, bool | int | float) else None

    return (
        span.parent_id is None
        and query.started_after <= span.started_at < query.started_before
        and all(text(span.attributes.get(key)) == value for key, value in query.attributes.items())
    )


def summary(span: Span) -> Span:
    return span.model_copy(
        update={
            "status_message": redact_json(span.status_message),
            "input": None,
            "output": None,
            "events": [],
            "links": [],
            "resource_attributes": {},
            "attributes": redact_json(
                {
                    key: value
                    for key, value in span.attributes.items()
                    if key not in {"a13n.input", "a13n.output", "gen_ai.input.messages", "gen_ai.output.messages"}
                }
            ),
        }
    )


def within(value: Decimal | None, minimum: Decimal | None, maximum: Decimal | None) -> bool:
    if minimum is None and maximum is None:
        return True
    return value is not None and (minimum is None or value >= minimum) and (maximum is None or value <= maximum)


def rank(items: list[TraceSearchItem], query: SearchQuery) -> list[TraceSearchItem]:
    selected = [
        item
        for item in items
        if within(item.duration_seconds, query.min_duration_seconds, query.max_duration_seconds)
        and within(item.cost_usd, query.min_cost_usd, query.max_cost_usd)
    ]
    selected.sort(key=lambda item: (item.root.trace_id, item.root.id))

    def value(item: TraceSearchItem) -> datetime | Decimal | None:
        return (
            item.root.started_at
            if query.order_by == "started"
            else (item.duration_seconds if query.order_by == "duration" else item.cost_usd)
        )

    known = [item for item in selected if value(item) is not None]
    if query.order_by == "started":
        known.sort(key=lambda item: item.root.started_at, reverse=query.direction == "desc")
    else:
        known.sort(
            key=lambda item: (item.duration_seconds if query.order_by == "duration" else item.cost_usd) or Decimal(0),
            reverse=query.direction == "desc",
        )
    return known + [item for item in selected if value(item) is None]
