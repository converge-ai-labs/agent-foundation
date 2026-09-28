"""The trace HTTP surface: an attempt's spans, a workspace's traces for browsing, and the backend they come from."""

from typing import Annotated

from fastapi import APIRouter, Path, Query
from pydantic import AwareDatetime

from a13n_service.infra.http import PageLimit
from a13n_service.infra.ids import ObjectId
from a13n_service.providers.traces import Span, SpanPage
from a13n_service.runs import traces
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1", tags=["runs"])

TraceId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
IdFilter = Annotated[ObjectId | None, Query()]


@router.get("/runs/{run_id}/attempts/{attempt_id}/trace", response_model=SpanPage)
async def list_attempt_spans(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    run_id: str,
    attempt_id: str,
    actor: Actor,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> SpanPage:
    """The attempt's spans, including its inline child runs."""
    return await traces.list_attempt_spans(
        runtime.storage, runtime.traces, actor, workspace_id, run_id, attempt_id, limit=limit, cursor=cursor
    )


@router.get("/traces", response_model=SpanPage)
async def list_traces(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    actor: Actor,
    session_id: IdFilter = None,
    thread_id: IdFilter = None,
    run_id: IdFilter = None,
    attribute: Annotated[list[str] | None, Query(description="key:value, an exact root span attribute")] = None,
    started_after: AwareDatetime | None = None,
    started_before: AwareDatetime | None = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> SpanPage:
    """Trace root spans, one per attempt. A cursor keeps the window of the first page."""
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
        limit=limit,
        cursor=cursor,
    )


@router.get("/traces/{trace_id}", response_model=Span)
async def get_trace(runtime: CurrentRuntime, workspace_id: WorkspaceId, trace_id: TraceId, actor: Actor) -> Span:
    """The trace's root span."""
    return await traces.get_trace(runtime.storage, runtime.traces, actor, workspace_id, trace_id)


@router.get("/traces/{trace_id}/spans", response_model=SpanPage)
async def list_trace_spans(
    runtime: CurrentRuntime,
    workspace_id: WorkspaceId,
    trace_id: TraceId,
    actor: Actor,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> SpanPage:
    """The trace's spans."""
    return await traces.list_trace_spans(
        runtime.storage, runtime.traces, actor, workspace_id, trace_id, limit=limit, cursor=cursor
    )


@router.get("/trace-backend", response_model=traces.TraceBackend)
async def get_trace_backend(runtime: CurrentRuntime, workspace_id: WorkspaceId, actor: Actor) -> traces.TraceBackend:
    """The backend trace queries read, and how far back they find a trace."""
    return await traces.describe_backend(runtime.storage, runtime.traces, actor, workspace_id)
