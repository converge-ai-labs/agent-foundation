"""Trace query: an attempt's spans and a workspace's traces, read from the deployment's trace backend.

Scope is authorized before any supplied ID is resolved, and the database session closes before the backend is
called. Every returned span is checked against every attribute it was queried by, so a backend's filtering is
never trusted for tenancy. Credentials are redacted from returned spans by the rule the Harness applies to the
events that display folds.
"""

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import overload

from a13n_harness.observation import redact_json
from pydantic import BaseModel, JsonValue
from sqlalchemy import select

from a13n_service.infra import cursors, reads
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import ServiceError, invalid, not_found
from a13n_service.infra.telemetry import correlation_attributes
from a13n_service.providers.traces import Span, SpanPage, SpanQuery, TraceBackendType, TraceProvider
from a13n_service.runs import trace_reads
from a13n_service.runs.tables import AttemptRow
from a13n_service.runs.threads import get_run
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope

# The widest listing window, and how far back a trace is found by ID; backends keep about a month.
MAX_WINDOW = timedelta(days=31)
DEFAULT_WINDOW = timedelta(days=1)
# An attempt's spans start while it runs; the margin absorbs clock skew between workers and the database.
CLOCK_SKEW = timedelta(minutes=1)
MAX_ATTRIBUTE_SELECTORS = 8
# The Service and Harness stamp tenant correlation in this attribute namespace; a caller's selector never names it.
_RESERVED_ATTRIBUTES = "a13n."


class TraceBackend(BaseModel):
    """The trace backend queries read; `type` is null when trace query is not configured."""

    type: TraceBackendType | None
    # The earliest start at which a trace is found by ID.
    queryable_since: datetime | None


@overload
async def list_attempt_spans(
    storage: Storage,
    traces: TraceProvider | None,
    actor: Principal,
    workspace_id: str,
    run_id: str,
    attempt_id: str,
    *,
    limit: int,
    cursor: str | None,
    selection: None = None,
) -> SpanPage: ...


@overload
async def list_attempt_spans(
    storage: Storage,
    traces: TraceProvider | None,
    actor: Principal,
    workspace_id: str,
    run_id: str,
    attempt_id: str,
    *,
    limit: int,
    cursor: str | None,
    selection: trace_reads.Selection,
) -> trace_reads.SpanReadPage: ...


@overload
async def list_attempt_spans(
    storage: Storage,
    traces: TraceProvider | None,
    actor: Principal,
    workspace_id: str,
    run_id: str,
    attempt_id: str,
    *,
    limit: int,
    cursor: str | None,
    selection: trace_reads.Selection | None = None,
) -> SpanPage | trace_reads.SpanReadPage: ...


async def list_attempt_spans(
    storage: Storage,
    traces: TraceProvider | None,
    actor: Principal,
    workspace_id: str,
    run_id: str,
    attempt_id: str,
    *,
    limit: int,
    cursor: str | None,
    selection: trace_reads.Selection | None = None,
) -> SpanPage | trace_reads.SpanReadPage:
    """Every span the attempt's Harness run recorded, including its inline child runs."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        run = await get_run(session, scope.workspace_id, run_id)
        attempt = await session.scalar(
            select(AttemptRow).where(AttemptRow.run_id == run.id, AttemptRow.id == attempt_id)
        )
        if attempt is None:
            raise not_found("attempt", attempt_id)
    correlation = correlation_attributes(
        scope.organization_id, scope.workspace_id, run_id=run.id, run_attempt_id=attempt.id
    )
    ended = attempt.finished_at or datetime.now(UTC)
    query = SpanQuery(correlation, attempt.created_at - CLOCK_SKEW, ended + CLOCK_SKEW, limit)
    return await _read(_available(traces), query, cursor=cursor, selection=selection)


@overload
async def list_traces(
    storage: Storage,
    traces: TraceProvider | None,
    actor: Principal,
    workspace_id: str,
    *,
    session_id: str | None,
    thread_id: str | None,
    run_id: str | None,
    attributes: Sequence[str],
    started_after: datetime | None,
    started_before: datetime | None,
    limit: int,
    cursor: str | None,
    selection: None = None,
) -> SpanPage: ...


@overload
async def list_traces(
    storage: Storage,
    traces: TraceProvider | None,
    actor: Principal,
    workspace_id: str,
    *,
    session_id: str | None,
    thread_id: str | None,
    run_id: str | None,
    attributes: Sequence[str],
    started_after: datetime | None,
    started_before: datetime | None,
    limit: int,
    cursor: str | None,
    selection: trace_reads.Selection,
) -> trace_reads.SpanReadPage: ...


@overload
async def list_traces(
    storage: Storage,
    traces: TraceProvider | None,
    actor: Principal,
    workspace_id: str,
    *,
    session_id: str | None,
    thread_id: str | None,
    run_id: str | None,
    attributes: Sequence[str],
    started_after: datetime | None,
    started_before: datetime | None,
    limit: int,
    cursor: str | None,
    selection: trace_reads.Selection | None = None,
) -> SpanPage | trace_reads.SpanReadPage: ...


async def list_traces(
    storage: Storage,
    traces: TraceProvider | None,
    actor: Principal,
    workspace_id: str,
    *,
    session_id: str | None,
    thread_id: str | None,
    run_id: str | None,
    attributes: Sequence[str],
    started_after: datetime | None,
    started_before: datetime | None,
    limit: int,
    cursor: str | None,
    selection: trace_reads.Selection | None = None,
) -> SpanPage | trace_reads.SpanReadPage:
    """Trace roots, one per attempt; the window defaults to the last day.

    Each of `attributes`, a `key:value` selector, matches one string attribute of the root span exactly.
    """
    scope = await _read_scope(storage, actor, workspace_id)
    before = started_before or datetime.now(UTC)
    after = started_after or before - DEFAULT_WINDOW
    if not timedelta(0) < before - after <= MAX_WINDOW:
        raise invalid("started_after", "the window must end after it starts and span at most 31 days")
    selected = _selected_attributes(attributes)
    correlation = correlation_attributes(
        scope.organization_id, scope.workspace_id, session_id=session_id, thread_id=thread_id, run_id=run_id
    )
    query = SpanQuery({**selected, **correlation}, after, before, limit, roots=True)
    return await _read(
        _available(traces), query, cursor=cursor, requested=(started_after, started_before), selection=selection
    )


async def get_trace(
    storage: Storage, traces: TraceProvider | None, actor: Principal, workspace_id: str, trace_id: str
) -> Span:
    """The trace's root span."""
    scope = await _read_scope(storage, actor, workspace_id)
    query = _trace_query(scope, trace_id, 1, roots=True)
    page = await _read(_available(traces), query, cursor=None)
    if not page.items:
        raise not_found("trace", trace_id)
    assert isinstance(page.items[0], Span)
    return page.items[0]


@overload
async def list_trace_spans(
    storage: Storage,
    traces: TraceProvider | None,
    actor: Principal,
    workspace_id: str,
    trace_id: str,
    *,
    limit: int,
    cursor: str | None,
    selection: None = None,
) -> SpanPage: ...


@overload
async def list_trace_spans(
    storage: Storage,
    traces: TraceProvider | None,
    actor: Principal,
    workspace_id: str,
    trace_id: str,
    *,
    limit: int,
    cursor: str | None,
    selection: trace_reads.Selection,
) -> trace_reads.SpanReadPage: ...


@overload
async def list_trace_spans(
    storage: Storage,
    traces: TraceProvider | None,
    actor: Principal,
    workspace_id: str,
    trace_id: str,
    *,
    limit: int,
    cursor: str | None,
    selection: trace_reads.Selection | None = None,
) -> SpanPage | trace_reads.SpanReadPage: ...


async def list_trace_spans(
    storage: Storage,
    traces: TraceProvider | None,
    actor: Principal,
    workspace_id: str,
    trace_id: str,
    *,
    limit: int,
    cursor: str | None,
    selection: trace_reads.Selection | None = None,
) -> SpanPage | trace_reads.SpanReadPage:
    scope = await _read_scope(storage, actor, workspace_id)
    query = _trace_query(scope, trace_id, limit)
    return await _read(_available(traces), query, cursor=cursor, selection=selection)


async def describe_backend(
    storage: Storage, traces: TraceProvider | None, actor: Principal, workspace_id: str
) -> TraceBackend:
    await _read_scope(storage, actor, workspace_id)
    if traces is None:
        return TraceBackend(type=None, queryable_since=None)
    return TraceBackend(type=traces.type, queryable_since=datetime.now(UTC) - MAX_WINDOW)


async def _read_scope(storage: Storage, actor: Principal, workspace_id: str) -> WorkspaceScope:
    async with short_session(storage) as session:
        return await workspace_scope(session, actor, workspace_id, "read")


def _available(traces: TraceProvider | None) -> TraceProvider:
    if traces is None:
        raise ServiceError("unavailable", "Trace query is not configured", {"dependency": "trace"})
    return traces


def _selected_attributes(selectors: Sequence[str]) -> dict[str, str]:
    """Selectors split at their first colon: OpenTelemetry keys are dotted and hold none, while values may."""
    if len(selectors) > MAX_ATTRIBUTE_SELECTORS:
        raise invalid("attribute", f"at most {MAX_ATTRIBUTE_SELECTORS} attribute selectors")
    selected: dict[str, str] = {}
    for selector in selectors:
        key, separator, value = selector.partition(":")
        if not separator or not key or len(key) > 128 or len(value) > 256:
            raise invalid("attribute", "selectors are key:value")
        if key.startswith(_RESERVED_ATTRIBUTES):
            raise invalid("attribute", "a13n. attributes are selected by session_id, thread_id and run_id")
        # Results would otherwise confirm a credential that returned spans redact.
        if redact_json({key: value}) != {key: value}:
            raise invalid("attribute", "credentials are not selectable")
        if key in selected:
            raise invalid("attribute", "each attribute is selected once")
        selected[key] = value
    return selected


def _trace_query(scope: WorkspaceScope, trace_id: str, limit: int, *, roots: bool = False) -> SpanQuery:
    correlation = correlation_attributes(scope.organization_id, scope.workspace_id)
    now = datetime.now(UTC)
    return SpanQuery(correlation, now - MAX_WINDOW, now, limit, trace_id=trace_id, roots=roots)


async def _read(
    provider: TraceProvider,
    query: SpanQuery,
    *,
    cursor: str | None,
    requested: tuple[datetime | None, datetime | None] = (None, None),
    selection: trace_reads.Selection | None = None,
) -> SpanPage | trace_reads.SpanReadPage:
    """One page of matching spans; the cursor keeps the backend's position and the first page's resolved window.

    `requested` is the window as given; a cursor continues only the same request, so a changed window is refused.
    """
    owner = cursors.query_owner(sorted(query.attributes.items()), query.trace_id, query.roots, requested)
    if selection is not None:
        owner = cursors.query_owner(owner, selection.model_dump())
    if cursor is not None:
        position, after, before = _decode(cursor, owner)
        query = replace(query, cursor=position, started_after=after, started_before=before)
    values: list[trace_reads.SpanSummary | Span] = []
    while True:
        page = await provider.query(query)
        items = [_redact(span) for span in page.items if _matches(span, query)]
        if selection is None:
            break
        selected = [span for span in items if selection.matches(span)]
        values = [trace_reads.summary(span) if selection.view == "compact" else span for span in selected]
        # Retry with a smaller backend page before returning anything. Its own cursor then remains exact.
        count = reads.fit_count(values)
        if count == len(values):
            break
        query = replace(query, limit=max(1, min(count, query.limit - 1)))
    next_cursor = None
    if page.next_cursor is not None:
        resolved = (query.started_after.isoformat(), query.started_before.isoformat())
        next_cursor = cursors.encode("spans", owner, page.next_cursor, *resolved)
    if selection is not None:
        return trace_reads.SpanReadPage(items=values, next_cursor=next_cursor)
    return SpanPage(items=items, next_cursor=next_cursor)


def _decode(cursor: str, owner: str) -> tuple[str, datetime, datetime]:
    match cursors.decode(cursor, "spans", owner):
        case [str(position), str(after), str(before)]:
            try:
                start, end = datetime.fromisoformat(after), datetime.fromisoformat(before)
            except ValueError:
                pass
            else:
                if start.tzinfo and end.tzinfo and timedelta(0) < end - start <= MAX_WINDOW:
                    return position, start, end
    raise ServiceError("invalid_cursor", "Invalid collection cursor")


def _matches(span: Span, query: SpanQuery) -> bool:
    return (
        all(_text(span.attributes.get(key)) == value for key, value in query.attributes.items())
        and (query.trace_id is None or span.trace_id == query.trace_id)
        and not (query.roots and span.parent_id is not None)
    )


def _text(value: JsonValue) -> str | None:
    """An attribute as the backends compare it with a selector: a string as is, a number or boolean as JSON."""
    if isinstance(value, str):
        return value
    return json.dumps(value) if isinstance(value, bool | int | float) else None


def _redact(span: Span) -> Span:
    return span.model_copy(
        update={
            "status_message": redact_json(span.status_message),
            "input": redact_json(span.input),
            "output": redact_json(span.output),
            "attributes": redact_json(span.attributes),
            "resource_attributes": redact_json(span.resource_attributes),
            "events": [event.model_copy(update={"attributes": redact_json(event.attributes)}) for event in span.events],
            "links": [link.model_copy(update={"attributes": redact_json(link.attributes)}) for link in span.links],
        }
    )
