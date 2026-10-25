"""Schedule management shared by HTTP, external MCP and internal platform tools."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from a13n_service.infra.http import IdempotencyKey, IfMatch, tagged
from a13n_service.infra.platform import platform_operation
from a13n_service.runs.requests import CurrentRuntime
from a13n_service.runs.schedules import dispatch, queries, service
from a13n_service.runs.schedules.schemas import (
    Occurrence,
    OccurrencePage,
    OccurrenceQuery,
    Schedule,
    ScheduleCommand,
    ScheduleCreate,
    SchedulePage,
    SchedulePreview,
    SchedulePreviewRequest,
    ScheduleQuery,
    ScheduleStatus,
    ScheduleUpdate,
)
from a13n_service.runs.schemas import context_response
from a13n_service.tenancy.requests import Actor, WorkspaceId

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])


@router.post("", response_model=Schedule, status_code=201, openapi_extra=platform_operation("schedules", "write"))
async def create_schedule(
    response: Response,
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    body: ScheduleCreate,
    key: IdempotencyKey,
) -> Schedule:
    """Save durable execution authority as the creator, beyond this credential or Run's lifetime.

    Defaults to paused. Set state=enabled only when automatic future execution is intended.
    """
    result, created = await service.create(runtime, actor, workspace_id, body, request_key=key)
    response.status_code = 201 if created else 200
    return context_response(tagged(response, result))


@router.get("", response_model=SchedulePage, openapi_extra=platform_operation("schedules", "read"))
async def list_schedules(
    workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime, filters: Annotated[ScheduleQuery, Query()]
) -> SchedulePage:
    return context_response(await queries.list_schedules(runtime.storage, actor, workspace_id, filters))


@router.post("/preview", response_model=SchedulePreview, openapi_extra=platform_operation("schedules", "read"))
async def preview_schedule(
    workspace_id: WorkspaceId, actor: Actor, runtime: CurrentRuntime, body: SchedulePreviewRequest
) -> SchedulePreview:
    return await service.preview(runtime, actor, workspace_id, body)


@router.get("/{schedule_id}", response_model=Schedule, openapi_extra=platform_operation("schedules", "read"))
async def get_schedule(
    response: Response, workspace_id: WorkspaceId, schedule_id: str, actor: Actor, runtime: CurrentRuntime
) -> Schedule:
    return context_response(tagged(response, await queries.get(runtime.storage, actor, workspace_id, schedule_id)))


@router.patch("/{schedule_id}", response_model=Schedule, openapi_extra=platform_operation("schedules", "write"))
async def update_schedule(
    response: Response,
    workspace_id: WorkspaceId,
    schedule_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    body: ScheduleUpdate,
    if_match: IfMatch = None,
) -> Schedule:
    return context_response(
        tagged(response, await service.update(runtime, actor, workspace_id, schedule_id, body, if_match=if_match))
    )


@router.post("/{schedule_id}/enable", response_model=Schedule, openapi_extra=platform_operation("schedules", "write"))
async def enable_schedule(
    response: Response,
    workspace_id: WorkspaceId,
    schedule_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    body: ScheduleCommand,
    if_match: IfMatch = None,
) -> Schedule:
    return context_response(
        tagged(
            response, await service.transition(runtime, actor, workspace_id, schedule_id, "enable", if_match=if_match)
        )
    )


@router.post("/{schedule_id}/pause", response_model=Schedule, openapi_extra=platform_operation("schedules", "write"))
async def pause_schedule(
    response: Response,
    workspace_id: WorkspaceId,
    schedule_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    body: ScheduleCommand,
    if_match: IfMatch = None,
) -> Schedule:
    return context_response(
        tagged(
            response, await service.transition(runtime, actor, workspace_id, schedule_id, "pause", if_match=if_match)
        )
    )


@router.delete("/{schedule_id}", status_code=204, openapi_extra=platform_operation("schedules", "write"))
async def archive_schedule(
    workspace_id: WorkspaceId, schedule_id: str, actor: Actor, runtime: CurrentRuntime, if_match: IfMatch = None
) -> Response:
    await service.transition(runtime, actor, workspace_id, schedule_id, "archive", if_match=if_match)
    return Response(status_code=204)


@router.get(
    "/{schedule_id}/status", response_model=ScheduleStatus, openapi_extra=platform_operation("schedules", "read")
)
async def get_schedule_status(
    workspace_id: WorkspaceId, schedule_id: str, actor: Actor, runtime: CurrentRuntime
) -> ScheduleStatus:
    return await queries.status(runtime.storage, actor, workspace_id, schedule_id)


@router.get(
    "/{schedule_id}/occurrences", response_model=OccurrencePage, openapi_extra=platform_operation("schedules", "read")
)
async def list_schedule_occurrences(
    workspace_id: WorkspaceId,
    schedule_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    filters: Annotated[OccurrenceQuery, Query()],
) -> OccurrencePage:
    return await queries.list_occurrences(runtime.storage, actor, workspace_id, schedule_id, filters)


@router.get(
    "/{schedule_id}/occurrences/{occurrence_id}",
    response_model=Occurrence,
    openapi_extra=platform_operation("schedules", "read"),
)
async def get_schedule_occurrence(
    workspace_id: WorkspaceId,
    schedule_id: str,
    occurrence_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    include: Literal["execution"] | None = None,
) -> Occurrence:
    return context_response(
        await queries.get_occurrence(
            runtime.storage, actor, workspace_id, schedule_id, occurrence_id, execution=include == "execution"
        )
    )


@router.post(
    "/{schedule_id}/dispatch",
    response_model=Occurrence,
    status_code=202,
    openapi_extra=platform_operation("schedules", "write"),
)
async def dispatch_schedule(
    response: Response,
    workspace_id: WorkspaceId,
    schedule_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    body: ScheduleCommand,
    key: IdempotencyKey,
    if_match: IfMatch = None,
) -> Occurrence:
    result, created = await dispatch.manual(
        runtime, actor, workspace_id, schedule_id, request_key=key, if_match=if_match
    )
    response.status_code = 202 if created else 200
    return context_response(result)
