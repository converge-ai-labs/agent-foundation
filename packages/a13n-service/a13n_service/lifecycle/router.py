"""Native lifecycle reconciliation API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.request_runtime import get_control_runtime

from .domain import ResourceLifecycleEventPage, WorkspaceEventPage
from .service import LifecycleEventError, LifecycleEventService

router = APIRouter(prefix="/api/v1", tags=["lifecycle-events"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


def _service(request: Request) -> LifecycleEventService:
    control = get_control_runtime(request)
    if control is None:
        raise LifecycleEventError(
            "lifecycle_events_unavailable",
            "Lifecycle events are unavailable.",
            category=ErrorCategory.unavailable,
        )
    return control.lifecycle_events


@router.get("/workspaces/{workspace_id}/events", response_model=WorkspaceEventPage)
async def list_workspace_events(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> WorkspaceEventPage:
    return await _service(request).list_workspace_events(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/runs/{run_id}/events", response_model=ResourceLifecycleEventPage)
async def list_run_events(
    request: Request,
    actor: Actor,
    run_id: str,
    after_resource_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ResourceLifecycleEventPage:
    return await _service(request).list_run_events(
        actor=actor,
        run_id=run_id,
        after_resource_seq=after_resource_seq,
        limit=limit,
    )


@router.get("/run-attempts/{run_attempt_id}/events", response_model=ResourceLifecycleEventPage)
async def list_run_attempt_events(
    request: Request,
    actor: Actor,
    run_attempt_id: str,
    after_resource_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ResourceLifecycleEventPage:
    return await _service(request).list_run_attempt_events(
        actor=actor,
        run_attempt_id=run_attempt_id,
        after_resource_seq=after_resource_seq,
        limit=limit,
    )


__all__ = ["router"]
