"""Explicit execution platform tools, independent of HTTP operation admission."""

from typing import Annotated, Literal, get_args

from pydantic import AwareDatetime
from pydantic import Field as Path
from pydantic import Field as Query

from a13n_service.infra import reads
from a13n_service.infra.ids import ObjectId
from a13n_service.platform_tools import ToolContext as PlatformContext
from a13n_service.platform_tools import definition
from a13n_service.providers.traces import SpanPage
from a13n_service.runs import content_tools as content
from a13n_service.runs import trace_reads, trace_search, traces
from a13n_service.runs.findings import analysis
from a13n_service.runs.runtime import Runtime

type ToolContext = PlatformContext[Runtime]

TraceId = Annotated[str, Path(pattern="^[0-9a-f]{32}$")]
IdFilter = Annotated[ObjectId | None, Query()]


async def list_attempt_spans(
    ctx: ToolContext,
    *,
    run_id: str,
    attempt_id: str,
    status: Literal["ok", "error"] | None = None,
    kind: Annotated[str | None, Query(max_length=64)] = None,
    tool_name: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: str | None = None,
) -> SpanPage | trace_reads.SpanReadPage:
    """The attempt's spans, including its inline child runs."""
    await analysis.read_scope(ctx.runtime, ctx.actor, ctx.workspace_id, ctx.origin, None)
    return await traces.list_attempt_spans(
        ctx.runtime.storage,
        ctx.runtime.traces,
        ctx.actor,
        ctx.workspace_id,
        run_id,
        attempt_id,
        limit=reads.page_limit("compact", limit),
        cursor=cursor,
        selection=trace_reads.Selection(view="compact", status=status, kind=kind, tool_name=tool_name),
    )


async def list_traces(
    ctx: ToolContext,
    *,
    session_id: IdFilter = None,
    thread_id: IdFilter = None,
    run_id: IdFilter = None,
    attribute: Annotated[list[str] | None, Query(description="key:value, an exact root span attribute")] = None,
    started_after: AwareDatetime | None = None,
    started_before: AwareDatetime | None = None,
    status: Literal["ok", "error"] | None = None,
    kind: Annotated[str | None, Query(max_length=64)] = None,
    tool_name: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: str | None = None,
) -> SpanPage | trace_reads.SpanReadPage:
    """Trace root spans, one per attempt. A cursor keeps the window of the first page."""
    await analysis.read_scope(ctx.runtime, ctx.actor, ctx.workspace_id, ctx.origin, None)
    return await traces.list_traces(
        ctx.runtime.storage,
        ctx.runtime.traces,
        ctx.actor,
        ctx.workspace_id,
        session_id=session_id,
        thread_id=thread_id,
        run_id=run_id,
        attributes=attribute or (),
        started_after=started_after,
        started_before=started_before,
        limit=reads.page_limit("compact", limit),
        cursor=cursor,
        selection=trace_reads.Selection(view="compact", status=status, kind=kind, tool_name=tool_name),
    )


async def search_traces(
    ctx: ToolContext, *, filters: Annotated[trace_search.TraceSearch, Query()]
) -> trace_search.TraceSearchPage:
    """Rank and filter the entire root-start window before paging. Costs sum reported span costs, not billing.

    Providers execute each query without a result cache; pages may reflect updates between requests.
    Keyset cursors bind the ordering, filters and last sort value with trace/span IDs.
    Incomplete queries fail without a partial rank.
    """
    await analysis.read_scope(ctx.runtime, ctx.actor, ctx.workspace_id, ctx.origin, None)
    return await trace_search.search(ctx.runtime, ctx.actor, ctx.workspace_id, filters)


async def get_trace(ctx: ToolContext, *, trace_id: TraceId) -> content.ContentView[trace_reads.SpanSummary]:
    """Read a redacted observation summary and version-bound content references."""
    await analysis.read_scope(ctx.runtime, ctx.actor, ctx.workspace_id, ctx.origin, trace_id)
    span = await traces.get_trace(ctx.runtime.storage, ctx.runtime.traces, ctx.actor, ctx.workspace_id, trace_id)
    await analysis.record_read(ctx.runtime, ctx.actor, ctx.workspace_id, ctx.origin, trace_id)
    return content.ContentView(
        summary=trace_reads.summary(span),
        content={
            section: content.value_reference(
                ctx,
                content.SpanSource(trace_id=trace_id, span_id=span.id, section=section),
                span if section == "all" else getattr(span, section),
            )
            for section in get_args(content.SpanSection)
        },
    )


async def list_trace_spans(
    ctx: ToolContext,
    *,
    trace_id: TraceId,
    status: Literal["ok", "error"] | None = None,
    kind: Annotated[str | None, Query(max_length=64)] = None,
    tool_name: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    cursor: str | None = None,
) -> SpanPage | trace_reads.SpanReadPage:
    """List compact spans within a selected trace, optionally filtered by status, kind or tool name.

    Continue with the returned cursor; use get_trace_span for content references to a selected span.
    """
    await analysis.read_scope(ctx.runtime, ctx.actor, ctx.workspace_id, ctx.origin, trace_id)
    page = await traces.list_trace_spans(
        ctx.runtime.storage,
        ctx.runtime.traces,
        ctx.actor,
        ctx.workspace_id,
        trace_id,
        limit=reads.page_limit("compact", limit),
        cursor=cursor,
        selection=trace_reads.Selection(view="compact", status=status, kind=kind, tool_name=tool_name),
    )
    if page.items:
        await analysis.record_read(ctx.runtime, ctx.actor, ctx.workspace_id, ctx.origin, trace_id)
    return page


async def get_trace_span(
    ctx: ToolContext, *, trace_id: TraceId, span_id: Annotated[str, Path(pattern="^[0-9a-f]{16}$")]
) -> content.ContentView[trace_reads.SpanSummary]:
    """Read a redacted observation summary and version-bound content references."""
    await analysis.read_scope(ctx.runtime, ctx.actor, ctx.workspace_id, ctx.origin, trace_id)
    span = await traces.get_trace_span(
        ctx.runtime.storage, ctx.runtime.traces, ctx.actor, ctx.workspace_id, trace_id, span_id
    )
    await analysis.record_read(ctx.runtime, ctx.actor, ctx.workspace_id, ctx.origin, trace_id)
    return content.ContentView(
        summary=trace_reads.summary(span),
        content={
            section: content.value_reference(
                ctx,
                content.SpanSource(trace_id=trace_id, span_id=span.id, section=section),
                span if section == "all" else getattr(span, section),
            )
            for section in get_args(content.SpanSection)
        },
    )


async def get_trace_backend(ctx: ToolContext) -> traces.TraceBackend:
    """The backend trace queries read, and how far back they find a trace."""
    return await traces.describe_backend(ctx.runtime.storage, ctx.runtime.traces, ctx.actor, ctx.workspace_id)


TOOLS = (
    definition(list_attempt_spans, group="execution", effect="read"),
    definition(list_traces, group="execution", effect="read"),
    definition(search_traces, group="execution", effect="read"),
    definition(get_trace, group="execution", effect="read"),
    definition(list_trace_spans, group="execution", effect="read"),
    definition(get_trace_span, group="execution", effect="read"),
    definition(get_trace_backend, group="execution", effect="read"),
)
