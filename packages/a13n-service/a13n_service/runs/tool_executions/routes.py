"""Retained execution controls for existing Thread and Run inspectors."""

from fastapi import APIRouter

from a13n_service.infra import reads
from a13n_service.infra.http import PageLimit
from a13n_service.infra.platform import platform_operation
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.runs.tool_executions import views
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/threads/{thread_id}/tool-executions", tags=["runs"])


@router.get("", response_model=views.ExecutionPage, openapi_extra=platform_operation("execution", "read"))
async def list_tool_executions(
    runtime: CurrentRuntime,
    actor: Actor,
    workspace_id: WorkspaceId,
    thread_id: str,
    run_id: str | None = None,
    active: bool | None = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> views.ExecutionPage:
    return await views.list_executions(
        runtime, actor, workspace_id, thread_id, run_id=run_id, active=active, limit=limit, cursor=cursor
    )


@router.get(
    "/{execution_id}", response_model=views.ExecutionView, openapi_extra=platform_operation("execution", "read")
)
async def get_tool_execution(
    runtime: CurrentRuntime,
    actor: Actor,
    workspace_id: WorkspaceId,
    thread_id: str,
    execution_id: str,
) -> views.ExecutionView:
    return await views.get_execution(runtime, actor, workspace_id, thread_id, execution_id)


@router.get(
    "/{execution_id}/result", response_model=reads.TextRead, openapi_extra=platform_operation("execution", "read")
)
async def get_tool_execution_result(
    runtime: CurrentRuntime,
    actor: Actor,
    workspace_id: WorkspaceId,
    thread_id: str,
    execution_id: str,
    offset: reads.Offset = 0,
    limit: reads.TextLimit = 4000,
    version: reads.Version = None,
) -> reads.TextRead:
    return await views.result(
        runtime, actor, workspace_id, thread_id, execution_id, offset=offset, limit=limit, version=version
    )


# Live output observation is deliberately outside MCP management admission.
@router.get("/{execution_id}/observation", response_model=views.ExecutionObservation)
async def observe_tool_execution(
    runtime: CurrentRuntime,
    actor: Actor,
    workspace_id: WorkspaceId,
    thread_id: str,
    execution_id: str,
) -> views.ExecutionObservation:
    return await views.observe(runtime, actor, workspace_id, thread_id, execution_id)


# Execution cancellation is deliberately outside MCP management admission.
@router.post("/{execution_id}/cancel", response_model=views.ExecutionView, status_code=202)
async def cancel_tool_execution(
    runtime: CurrentRuntime,
    actor: Actor,
    workspace_id: WorkspaceId,
    thread_id: str,
    execution_id: str,
) -> views.ExecutionView:
    return await views.cancel(runtime, actor, workspace_id, thread_id, execution_id)
