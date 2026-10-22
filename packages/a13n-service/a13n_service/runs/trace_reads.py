"""Small trace representations and explicit selection, independent of a backend's field layout."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from a13n_service.infra import reads
from a13n_service.providers.traces import Span


class SpanOverview(BaseModel):
    """Exact timeline and cost fields; payloads are read from the individual span."""

    view: Literal["overview"] = "overview"
    trace_id: str
    id: str
    parent_id: str | None
    name: str
    kind: str
    status: Literal["ok", "error"]
    level: str | None
    started_at: datetime
    ended_at: datetime | None
    model: str | None
    cost_usd: Decimal | None


def overview(span: Span) -> SpanOverview:
    return SpanOverview.model_validate(span, from_attributes=True)


class SpanSummary(BaseModel):
    view: Literal["compact"] = "compact"
    trace_id: str
    id: str
    parent_id: str | None
    name: reads.Preview
    kind: str
    status: Literal["ok", "error"]
    started_at: datetime
    ended_at: datetime | None
    duration_ms: float | None
    model: reads.Preview
    usage: reads.Preview
    cost_usd: Decimal | None
    error: reads.Preview
    omitted_sections: tuple[str, ...] = (
        "input",
        "output",
        "attributes",
        "resource_attributes",
        "events",
        "links",
        "usage",
    )


class SpanReadPage(BaseModel):
    items: list[SpanSummary | SpanOverview | Span]
    next_cursor: str | None


class SpanContentPage(BaseModel):
    """A matching span's selected content, or a continuation to keep locating that span."""

    items: list[reads.TextRead]
    next_cursor: str | None


class Selection(BaseModel):
    view: Literal["detailed", "compact", "overview"] = "detailed"
    status: Literal["ok", "error"] | None = None
    kind: str | None = None
    tool_name: str | None = None
    span_id: str | None = None

    def matches(self, span: Span) -> bool:
        return (
            (self.status is None or span.status == self.status)
            and (self.kind is None or span.kind == self.kind)
            and (
                self.tool_name is None
                or (
                    span.kind == "tool"
                    and (span.attributes.get("gen_ai.tool.name") == self.tool_name or span.name == self.tool_name)
                )
            )
            and (self.span_id is None or span.id == self.span_id)
        )


def summary(span: Span) -> SpanSummary:
    return reads.bounded(
        SpanSummary(
            trace_id=span.trace_id,
            id=span.id,
            parent_id=span.parent_id,
            name=reads.preview(span.name, 200),
            kind=span.kind,
            status=span.status,
            started_at=span.started_at,
            ended_at=span.ended_at,
            duration_ms=(span.ended_at - span.started_at).total_seconds() * 1000 if span.ended_at else None,
            model=reads.preview(span.model, 200),
            usage=reads.preview(span.usage),
            cost_usd=span.cost_usd,
            error=reads.preview(span.status_message, 500),
        )
    )
