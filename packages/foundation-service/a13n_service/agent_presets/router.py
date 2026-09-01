"""AgentPreset management routes under `/api/v1`."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, status

from a13n_service.iam import AuthenticatedActor, authenticate_request

from .domain import (
    AgentPreset,
    AgentPresetCollection,
    AgentPresetCommandRequest,
    AgentPresetLifecycleState,
    AgentPresetPublishResult,
    AgentPresetRevision,
    AgentPresetRevisionCollection,
    AgentPresetSource,
    CreateAgentPresetRequest,
    DuplicateAgentPresetRequest,
    PatchAgentPresetRequest,
    ReplaceAgentPresetConfigRequest,
    RollbackAgentPresetRequest,
)
from .errors import AgentPresetError
from .service import AgentPresetService

router = APIRouter(prefix="/api/v1", tags=["agent-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)]


def _service(request: Request) -> AgentPresetService:
    service: AgentPresetService | None = getattr(request.app.state, "agent_preset_service", None)
    if service is None:
        raise AgentPresetError("agent_management_unavailable", "Agent Management is unavailable.", status_code=503)
    return service


@router.get("/workspaces/{workspace_id}/agent-presets", response_model=AgentPresetCollection)
async def list_agent_presets(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    lifecycle_state: AgentPresetLifecycleState | None = None,
    source: AgentPresetSource | None = None,
    include_archived: bool = False,
) -> AgentPresetCollection:
    return await _service(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        lifecycle_state=lifecycle_state,
        source=source,
        include_archived=include_archived,
    )


@router.post(
    "/workspaces/{workspace_id}/agent-presets",
    response_model=AgentPreset,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_preset(
    request: Request,
    actor: Actor,
    workspace_id: str,
    body: CreateAgentPresetRequest,
    idempotency_key: IdempotencyKey,
) -> AgentPreset:
    return await _service(request).create(
        actor=actor,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        request=body,
    )


@router.get("/agent-presets/{agent_preset_id}", response_model=AgentPreset)
async def get_agent_preset(request: Request, actor: Actor, agent_preset_id: str) -> AgentPreset:
    return await _service(request).get(actor=actor, preset_id=agent_preset_id)


@router.patch("/agent-presets/{agent_preset_id}", response_model=AgentPreset)
async def patch_agent_preset(
    request: Request,
    actor: Actor,
    agent_preset_id: str,
    body: PatchAgentPresetRequest,
) -> AgentPreset:
    return await _service(request).patch_metadata(actor=actor, preset_id=agent_preset_id, request=body)


@router.put("/agent-presets/{agent_preset_id}/config", response_model=AgentPreset)
async def replace_agent_preset_config(
    request: Request,
    actor: Actor,
    agent_preset_id: str,
    body: ReplaceAgentPresetConfigRequest,
) -> AgentPreset:
    return await _service(request).replace_config(actor=actor, preset_id=agent_preset_id, request=body)


@router.post("/agent-presets/{agent_preset_id}/publish", response_model=AgentPresetPublishResult)
async def publish_agent_preset(
    request: Request,
    actor: Actor,
    agent_preset_id: str,
    body: AgentPresetCommandRequest,
    idempotency_key: IdempotencyKey,
) -> AgentPresetPublishResult:
    return await _service(request).publish(
        actor=actor,
        preset_id=agent_preset_id,
        idempotency_key=idempotency_key,
        request=body,
    )


@router.post("/agent-presets/{agent_preset_id}/rollback", response_model=AgentPresetPublishResult)
async def rollback_agent_preset(
    request: Request,
    actor: Actor,
    agent_preset_id: str,
    body: RollbackAgentPresetRequest,
    idempotency_key: IdempotencyKey,
) -> AgentPresetPublishResult:
    return await _service(request).rollback(
        actor=actor,
        preset_id=agent_preset_id,
        idempotency_key=idempotency_key,
        request=body,
    )


@router.post(
    "/agent-presets/{agent_preset_id}/duplicate",
    response_model=AgentPreset,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_agent_preset(
    request: Request,
    actor: Actor,
    agent_preset_id: str,
    body: DuplicateAgentPresetRequest,
    idempotency_key: IdempotencyKey,
) -> AgentPreset:
    return await _service(request).duplicate(
        actor=actor,
        preset_id=agent_preset_id,
        idempotency_key=idempotency_key,
        request=body,
    )


@router.post("/agent-presets/{agent_preset_id}/{action}", response_model=AgentPreset)
async def change_agent_preset_lifecycle(
    request: Request,
    actor: Actor,
    agent_preset_id: str,
    action: Literal["enable", "disable", "archive", "unarchive"],
    body: AgentPresetCommandRequest,
    idempotency_key: IdempotencyKey,
) -> AgentPreset:
    return await _service(request).change_lifecycle(
        actor=actor,
        preset_id=agent_preset_id,
        action=action,
        idempotency_key=idempotency_key,
        request=body,
    )


@router.get(
    "/agent-presets/{agent_preset_id}/revisions",
    response_model=AgentPresetRevisionCollection,
)
async def list_agent_preset_revisions(
    request: Request,
    actor: Actor,
    agent_preset_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> AgentPresetRevisionCollection:
    return await _service(request).list_revisions(
        actor=actor,
        preset_id=agent_preset_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/agent-preset-revisions/{agent_preset_revision_id}", response_model=AgentPresetRevision)
async def get_agent_preset_revision(
    request: Request,
    actor: Actor,
    agent_preset_revision_id: str,
) -> AgentPresetRevision:
    return await _service(request).get_revision(actor=actor, revision_id=agent_preset_revision_id)
