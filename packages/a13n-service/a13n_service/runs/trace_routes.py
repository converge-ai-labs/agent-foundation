"""The trace HTTP surface: an attempt's spans, a workspace's traces for browsing, and the backend they come from.

An analysis Run's own requests read only the traces its analysis selected, and record each one they read.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query
from pydantic import AwareDatetime

from a13n_service.infra import reads
from a13n_service.infra.errors import invalid
from a13n_service.infra.ids import ObjectId
from a13n_service.infra.platform import platform_operation
from a13n_service.providers.traces import Span, SpanPage
from a13n_service.runs import trace_reads, traces
from a13n_service.runs.findings import analysis
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, OriginRun, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["runs"])

TraceId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
IdFilter = Annotated[ObjectId | None, Query()]


@router.get(
    "/runs/{run_id}/attempts/{attempt_id}/trace",
    response_model=SpanPage | trace_reads.SpanReadPage,
    openapi_extra=platform_operation("execution", "read"),
)
async def list_attempt_spans(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    run_id: str,
    attempt_id: str,
    actor: Actor,
    origin: OriginRun,
    view: reads.View = "detailed",
    status: Literal["ok", "error"] | None = None,
    kind: Annotated[str | None, Query(max_length=64)] = None,
    tool_name: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: str | None = None,
) -> SpanPage | trace_reads.SpanReadPage:
    """The attempt's spans, including its inline child runs."""
    await analysis.read_scope(runtime, actor, workspace_id, origin, None)
    return await traces.list_attempt_spans(
        runtime.storage,
        runtime.traces,
        actor,
        workspace_id,
        run_id,
        attempt_id,
        limit=reads.page_limit(view, limit),
        cursor=cursor,
        selection=trace_reads.Selection(view=view, status=status, kind=kind, tool_name=tool_name)
        if view == "compact" or any((status, kind, tool_name))
        else None,
    )


@router.get(
    "/traces", response_model=SpanPage | trace_reads.SpanReadPage, openapi_extra=platform_operation("execution", "read")
)
async def list_traces(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    actor: Actor,
    origin: OriginRun,
    session_id: IdFilter = None,
    thread_id: IdFilter = None,
    run_id: IdFilter = None,
    attribute: Annotated[list[str] | None, Query(description="key:value, an exact root span attribute")] = None,
    started_after: AwareDatetime | None = None,
    started_before: AwareDatetime | None = None,
    view: reads.View = "detailed",
    status: Literal["ok", "error"] | None = None,
    kind: Annotated[str | None, Query(max_length=64)] = None,
    tool_name: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: str | None = None,
) -> SpanPage | trace_reads.SpanReadPage:
    """Trace root spans, one per attempt. A cursor keeps the window of the first page."""
    await analysis.read_scope(runtime, actor, workspace_id, origin, None)
    return await traces.list_traces(
        runtime.storage,
        runtime.traces,
        actor,
        workspace_id,
        session_id=session_id,
        thread_id=thread_id,
        run_id=run_id,
        attributes=attribute or (),
        started_after=started_after,
        started_before=started_before,
        limit=reads.page_limit(view, limit),
        cursor=cursor,
        selection=trace_reads.Selection(view=view, status=status, kind=kind, tool_name=tool_name)
        if view == "compact" or any((status, kind, tool_name))
        else None,
    )


@router.get(
    "/traces/{trace_id}",
    response_model=Span | trace_reads.SpanSummary | reads.TextRead,
    openapi_extra=platform_operation("execution", "read"),
)
async def get_trace(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    trace_id: TraceId,
    actor: Actor,
    origin: OriginRun,
    view: reads.View = "detailed",
    section: Literal["input", "output", "attributes", "resource_attributes", "events", "links", "usage"] | None = None,
    offset: reads.Offset = 0,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> Span | trace_reads.SpanSummary | reads.TextRead:
    """The trace's root span."""
    await analysis.read_scope(runtime, actor, workspace_id, origin, trace_id)
    span = await traces.get_trace(runtime.storage, runtime.traces, actor, workspace_id, trace_id)
    await analysis.record_read(runtime, actor, workspace_id, origin, trace_id)
    if section is not None:
        return reads.read_value(
            getattr(span, section),
            source=f"traces/{trace_id}/{span.id}/{section}",
            offset=offset,
            limit=limit,
            version=version,
            query=query,
        )
    if offset or version or query:
        raise invalid("section", "Select a section for fragment or search parameters")
    return trace_reads.summary(span) if view == "compact" else span


@router.get(
    "/traces/{trace_id}/spans",
    response_model=SpanPage | trace_reads.SpanReadPage,
    openapi_extra=platform_operation("execution", "read"),
)
async def list_trace_spans(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    trace_id: TraceId,
    actor: Actor,
    origin: OriginRun,
    view: reads.View = "detailed",
    status: Literal["ok", "error"] | None = None,
    kind: Annotated[str | None, Query(max_length=64)] = None,
    tool_name: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: str | None = None,
) -> SpanPage | trace_reads.SpanReadPage:
    """The trace's spans."""
    await analysis.read_scope(runtime, actor, workspace_id, origin, trace_id)
    page = await traces.list_trace_spans(
        runtime.storage,
        runtime.traces,
        actor,
        workspace_id,
        trace_id,
        limit=reads.page_limit(view, limit),
        cursor=cursor,
        selection=trace_reads.Selection(view=view, status=status, kind=kind, tool_name=tool_name)
        if view == "compact" or any((status, kind, tool_name))
        else None,
    )
    if page.items:
        await analysis.record_read(runtime, actor, workspace_id, origin, trace_id)
    return page


@router.get("/trace-backend", response_model=traces.TraceBackend, openapi_extra=platform_operation("execution", "read"))
async def get_trace_backend(runtime: CurrentRuntime, workspace_id: WorkspaceId, actor: Actor) -> traces.TraceBackend:
    """The backend trace queries read, and how far back they find a trace."""
    return await traces.describe_backend(runtime.storage, runtime.traces, actor, workspace_id)


@router.get(
    "/traces/{trace_id}/spans/{span_id}/content",
    response_model=trace_reads.SpanContentPage,
    openapi_extra=platform_operation("execution", "read"),
)
async def read_span_content(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    trace_id: TraceId,
    span_id: Annotated[str, Path(pattern=r"^[0-9a-f]{16}$")],
    actor: Actor,
    origin: OriginRun,
    section: Literal["all", "input", "output", "attributes", "resource_attributes", "events", "links", "usage"] = "all",
    cursor: str | None = None,
    offset: reads.Offset = 0,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
    query: reads.Search = None,
) -> trace_reads.SpanContentPage:
    """Locate a span in bounded backend pages. Follow next_cursor when empty. Once found, retain that request's
    cursor while continuing fragments with version and next_offset."""
    await analysis.read_scope(runtime, actor, workspace_id, origin, trace_id)
    # Full span is needed only internally; projection happens after the original tenancy and redaction checks.
    page = await traces.list_trace_spans(
        runtime.storage, runtime.traces, actor, workspace_id, trace_id, limit=100, cursor=cursor
    )
    selected = [span for span in page.items if span.id == span_id]
    if selected:
        await analysis.record_read(runtime, actor, workspace_id, origin, trace_id)
    return trace_reads.SpanContentPage(
        items=[
            reads.read_value(
                span if section == "all" else getattr(span, section),
                source=f"traces/{trace_id}/{span_id}/{section}",
                offset=offset,
                limit=limit,
                version=version,
                query=query,
            )
            for span in selected
        ],
        next_cursor=None if selected else page.next_cursor,
    )
