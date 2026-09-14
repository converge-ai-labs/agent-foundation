"""Native Trace Query `/api/v1` routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.resource_dependencies import WorkspaceId
from a13n_service.request_runtime import get_control_runtime

from .domain import ObservationCollection, SearchIn, Trace, TraceCollection, TraceQueryDescriptor, TraceView
from .errors import TraceQueryError
from .service import TraceQueryService

router = APIRouter(prefix="/api/v1", tags=["trace-query"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


def _traces(request: Request) -> TraceQueryService:
    control = get_control_runtime(request)
    if control is None:
        raise TraceQueryError(
            "trace_query_unavailable", "Trace Query is unavailable.", category=ErrorCategory.unavailable
        )
    return control.trace_queries


@router.get("/workspaces/{workspace}/traces", response_model=TraceCollection)
async def list_traces(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    from_started_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_started_at: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=8192)] = None,
    query: Annotated[str | None, Query(max_length=512)] = None,
    search_in: SearchIn | None = None,
    thread_id: Annotated[str | None, Query(max_length=1024)] = None,
    run_id: Annotated[str | None, Query(max_length=1024)] = None,
    run_attempt_id: Annotated[str | None, Query(max_length=1024)] = None,
    view: TraceView = TraceView.compact,
) -> TraceCollection:
    return await _traces(request).list(
        actor=actor,
        workspace_id=workspace_id,
        from_started_at=from_started_at,
        to_started_at=to_started_at,
        limit=limit,
        cursor=cursor,
        query=query,
        search_in=search_in,
        thread_id=thread_id,
        run_id=run_id,
        run_attempt_id=run_attempt_id,
        view=view,
    )


@router.get("/workspaces/{workspace}/traces/{trace_id}", response_model=Trace)
async def get_trace(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    trace_id: Annotated[str, Path(min_length=1, max_length=512)],
    view: TraceView = TraceView.full,
) -> Trace:
    return await _traces(request).get(
        actor=actor,
        workspace_id=workspace_id,
        trace_id=trace_id,
        view=view,
    )


@router.get("/workspaces/{workspace}/traces/{trace_id}/observations", response_model=ObservationCollection)
async def list_trace_observations(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    trace_id: Annotated[str, Path(min_length=1, max_length=512)],
    view: TraceView = TraceView.compact,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=8192)] = None,
) -> ObservationCollection:
    return await _traces(request).list_observations(
        actor=actor,
        workspace_id=workspace_id,
        trace_id=trace_id,
        view=view,
        limit=limit,
        cursor=cursor,
    )


@router.get("/workspaces/{workspace}/trace-query", response_model=TraceQueryDescriptor)
async def get_trace_query(request: Request, actor: Actor, workspace_id: WorkspaceId) -> TraceQueryDescriptor:
    return await _traces(request).describe(actor=actor, workspace_id=workspace_id)
