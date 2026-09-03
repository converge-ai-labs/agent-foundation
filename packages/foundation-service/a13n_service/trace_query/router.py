"""Native Trace Query `/api/v1` routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.request_runtime import get_control_runtime

from .domain import SearchIn, TraceCollection, TraceDetail, TraceView
from .errors import TraceQueryError
from .service import TraceQueryService

router = APIRouter(prefix="/api/v1", tags=["trace-query"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


def _traces(request: Request) -> TraceQueryService:
    control = get_control_runtime(request)
    if control is None:
        raise TraceQueryError("trace_query_unavailable", "Trace Query is unavailable.", status_code=503)
    return control.trace_queries


@router.get("/workspaces/{workspace_id}/traces", response_model=TraceCollection)
async def list_traces(
    request: Request,
    actor: Actor,
    workspace_id: str,
    from_started_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_started_at: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=8192)] = None,
    query: Annotated[str | None, Query(max_length=512)] = None,
    search_in: SearchIn | None = None,
    thread_id: Annotated[str | None, Query(max_length=1024)] = None,
    run_id: Annotated[str | None, Query(max_length=1024)] = None,
    run_attempt_id: Annotated[str | None, Query(max_length=1024)] = None,
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
    )


@router.get("/workspaces/{workspace_id}/traces/{trace_id}", response_model=TraceDetail)
async def get_trace(
    request: Request,
    actor: Actor,
    workspace_id: str,
    trace_id: Annotated[str, Path(min_length=1, max_length=512)],
    view: TraceView = TraceView.full,
) -> TraceDetail:
    return await _traces(request).get(
        actor=actor,
        workspace_id=workspace_id,
        trace_id=trace_id,
        view=view,
    )
