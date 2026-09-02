"""Agent management routes under `/api/v1`."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request

from .domain import (
    Agent,
    AgentCollection,
    AgentRevision,
    AgentRevisionCollection,
    AgentRevisionCreateResult,
    AgentSource,
    CreateAgentRequest,
    CreateAgentRevisionRequest,
    DuplicateAgentRequest,
    RestoreAgentRevisionRequest,
    UpdateAgentRequest,
)
from .errors import AgentError
from .service import AgentService

router = APIRouter(prefix="/api/v1", tags=["agent-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]


def _service(request: Request) -> AgentService:
    service: AgentService | None = getattr(request.app.state, "agent_service", None)
    if service is None:
        raise AgentError("agent_management_unavailable", "Agent Management is unavailable.", status_code=503)
    return service


def _set_etag(response: Response, agent: Agent) -> None:
    response.headers["ETag"] = resource_etag(agent.id, agent.updated_at)


@router.get("/workspaces/{workspace_id}/agents", response_model=AgentCollection)
async def list_agents(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    enabled: bool | None = None,
    source: AgentSource | None = None,
    include_archived: bool = False,
) -> AgentCollection:
    return await _service(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        enabled=enabled,
        source=source,
        include_archived=include_archived,
    )


@router.post(
    "/workspaces/{workspace_id}/agents",
    response_model=AgentRevisionCreateResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
    body: CreateAgentRequest,
    idempotency_key: IdempotencyKey,
) -> AgentRevisionCreateResult:
    result = await _service(request).create(
        actor=actor,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _set_etag(response, result.agent)
    return result


@router.get("/agents/{agent_id}", response_model=Agent)
async def get_agent(request: Request, response: Response, actor: Actor, agent_id: str) -> Agent:
    agent = await _service(request).get(actor=actor, agent_id=agent_id)
    _set_etag(response, agent)
    return agent


@router.patch("/agents/{agent_id}", response_model=Agent)
async def update_agent(
    request: Request,
    response: Response,
    actor: Actor,
    agent_id: str,
    body: UpdateAgentRequest,
    if_match: IfMatch,
) -> Agent:
    agent = await _service(request).patch_metadata(
        actor=actor,
        agent_id=agent_id,
        if_match=if_match,
        request=body,
    )
    _set_etag(response, agent)
    return agent


@router.post(
    "/agents/{agent_id}/revisions",
    response_model=AgentRevisionCreateResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_revision(
    request: Request,
    response: Response,
    actor: Actor,
    agent_id: str,
    body: CreateAgentRevisionRequest,
    idempotency_key: IdempotencyKey,
) -> AgentRevisionCreateResult:
    result = await _service(request).create_revision(
        actor=actor,
        agent_id=agent_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _set_etag(response, result.agent)
    return result


@router.post(
    "/agents/{agent_id}/revisions/{revision_id}/restore",
    response_model=AgentRevisionCreateResult,
    status_code=status.HTTP_201_CREATED,
)
async def restore_agent_revision(
    request: Request,
    response: Response,
    actor: Actor,
    agent_id: str,
    revision_id: str,
    body: RestoreAgentRevisionRequest,
    idempotency_key: IdempotencyKey,
) -> AgentRevisionCreateResult:
    result = await _service(request).restore_revision(
        actor=actor,
        agent_id=agent_id,
        revision_id=revision_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _set_etag(response, result.agent)
    return result


@router.post(
    "/agents/{agent_id}/duplicate",
    response_model=Agent,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_agent(
    request: Request,
    response: Response,
    actor: Actor,
    agent_id: str,
    body: DuplicateAgentRequest,
    idempotency_key: IdempotencyKey,
) -> Agent:
    agent = await _service(request).duplicate(
        actor=actor,
        agent_id=agent_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _set_etag(response, agent)
    return agent


@router.post("/agents/{agent_id}/{action}", response_model=Agent)
async def change_agent_lifecycle(
    request: Request,
    response: Response,
    actor: Actor,
    agent_id: str,
    action: Literal["enable", "disable", "archive", "unarchive"],
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
) -> Agent:
    agent = await _service(request).change_lifecycle(
        actor=actor,
        agent_id=agent_id,
        action=action,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )
    _set_etag(response, agent)
    return agent


@router.get("/agents/{agent_id}/revisions", response_model=AgentRevisionCollection)
async def list_agent_revisions(
    request: Request,
    actor: Actor,
    agent_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> AgentRevisionCollection:
    return await _service(request).list_revisions(
        actor=actor,
        agent_id=agent_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/agent-revisions/{agent_revision_id}", response_model=AgentRevision)
async def get_agent_revision(
    request: Request,
    actor: Actor,
    agent_revision_id: str,
) -> AgentRevision:
    return await _service(request).get_revision(actor=actor, revision_id=agent_revision_id)
