"""Agent management routes under `/api/v1`."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import resource_etag
from a13n_service.http_images import IMAGE_UPLOAD, image_body, image_response
from a13n_service.http_types import IdempotencyKey
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.resource_dependencies import WorkspaceId, workspace_actor
from a13n_service.request_runtime import get_control_runtime, get_process_runtime

from .application import AgentManagement
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
from .http_dependencies import AgentId
from .toolset_service import ToolsetCandidate, ToolsetCandidateResult
from .toolsets import ToolsetCatalog

router = APIRouter(prefix="/api/v1", tags=["agent-management"])
Actor = Annotated[AuthenticatedActor, Depends(workspace_actor)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]


def _management(request: Request) -> AgentManagement:
    control = get_control_runtime(request)
    if control is None:
        raise AgentError(
            "agent_management_unavailable", "Agent Management is unavailable.", category=ErrorCategory.unavailable
        )
    return control.agents


def _set_etag(response: Response, agent: Agent) -> None:
    response.headers["ETag"] = resource_etag(agent.id, agent.updated_at)


@router.get("/workspaces/{workspace}/toolsets", response_model=ToolsetCatalog)
async def get_toolsets(request: Request, actor: Actor, workspace_id: WorkspaceId) -> ToolsetCatalog:
    return await _management(request).toolsets.definitions(actor=actor, workspace_id=workspace_id)


@router.post("/workspaces/{workspace}/toolsets/validate", response_model=ToolsetCandidateResult)
async def validate_toolsets(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    body: ToolsetCandidate,
) -> ToolsetCandidateResult:
    return await _management(request).toolsets.validate_candidate(
        actor=actor,
        workspace_id=workspace_id,
        candidate=body,
    )


@router.get("/workspaces/{workspace}/agents", response_model=AgentCollection)
async def list_agents(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    enabled: bool | None = None,
    source: AgentSource | None = None,
    include_archived: bool = False,
) -> AgentCollection:
    return await _management(request).queries.list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        enabled=enabled,
        source=source,
        include_archived=include_archived,
    )


@router.post(
    "/workspaces/{workspace}/agents",
    response_model=AgentRevisionCreateResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: WorkspaceId,
    body: CreateAgentRequest,
    idempotency_key: IdempotencyKey,
) -> AgentRevisionCreateResult:
    result = await _management(request).commands.create(
        actor=actor,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _set_etag(response, result.agent)
    return result


@router.get("/workspaces/{workspace}/agents/{agent}", response_model=Agent)
async def get_agent(request: Request, response: Response, actor: Actor, agent_id: AgentId) -> Agent:
    agent = await _management(request).queries.get(actor=actor, agent_id=agent_id)
    _set_etag(response, agent)
    return agent


@router.patch("/workspaces/{workspace}/agents/{agent}", response_model=Agent)
async def update_agent(
    request: Request,
    response: Response,
    actor: Actor,
    agent_id: AgentId,
    body: UpdateAgentRequest,
    if_match: IfMatch,
) -> Agent:
    agent = await _management(request).commands.patch_metadata(
        actor=actor,
        agent_id=agent_id,
        if_match=if_match,
        request=body,
    )
    _set_etag(response, agent)
    return agent


@router.post(
    "/workspaces/{workspace}/agents/{agent}/revisions",
    response_model=AgentRevisionCreateResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_revision(
    request: Request,
    response: Response,
    actor: Actor,
    agent_id: AgentId,
    body: CreateAgentRevisionRequest,
    idempotency_key: IdempotencyKey,
) -> AgentRevisionCreateResult:
    result = await _management(request).revisions.create_revision(
        actor=actor,
        agent_id=agent_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _set_etag(response, result.agent)
    return result


@router.post(
    "/workspaces/{workspace}/agents/{agent}/revisions/{revision_id}/restore",
    response_model=AgentRevisionCreateResult,
    status_code=status.HTTP_201_CREATED,
)
async def restore_agent_revision(
    request: Request,
    response: Response,
    actor: Actor,
    agent_id: AgentId,
    revision_id: str,
    body: RestoreAgentRevisionRequest,
    idempotency_key: IdempotencyKey,
) -> AgentRevisionCreateResult:
    result = await _management(request).revisions.restore_revision(
        actor=actor,
        agent_id=agent_id,
        revision_id=revision_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _set_etag(response, result.agent)
    return result


@router.post(
    "/workspaces/{workspace}/agents/{agent}/duplicate",
    response_model=Agent,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_agent(
    request: Request,
    response: Response,
    actor: Actor,
    agent_id: AgentId,
    body: DuplicateAgentRequest,
    idempotency_key: IdempotencyKey,
) -> Agent:
    agent = await _management(request).duplication.duplicate(
        actor=actor,
        agent_id=agent_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _set_etag(response, agent)
    return agent


@router.post("/workspaces/{workspace}/agents/{agent}/{action}", response_model=Agent)
async def change_agent_lifecycle(
    request: Request,
    response: Response,
    actor: Actor,
    agent_id: AgentId,
    action: Literal["enable", "disable", "archive", "unarchive"],
    idempotency_key: IdempotencyKey,
    if_match: IfMatch,
) -> Agent:
    agent = await _management(request).commands.change_lifecycle(
        actor=actor,
        agent_id=agent_id,
        action=action,
        idempotency_key=idempotency_key,
        if_match=if_match,
    )
    _set_etag(response, agent)
    return agent


@router.get("/workspaces/{workspace}/agents/{agent}/revisions", response_model=AgentRevisionCollection)
async def list_agent_revisions(
    request: Request,
    actor: Actor,
    agent_id: AgentId,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> AgentRevisionCollection:
    return await _management(request).queries.list_revisions(
        actor=actor,
        agent_id=agent_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/agent-revisions/{agent_revision_id}", response_model=AgentRevision)
async def get_agent_revision(
    request: Request,
    actor: Annotated[AuthenticatedActor, Depends(authenticate_request)],
    agent_revision_id: str,
) -> AgentRevision:
    return await _management(request).queries.get_revision(actor=actor, revision_id=agent_revision_id)


def _objects(request: Request):
    runtime = get_process_runtime(request)
    if runtime is None:
        raise AgentError(
            "agent_management_unavailable", "Agent Management is unavailable.", category=ErrorCategory.unavailable
        )
    return runtime.shared.storage.objects


@router.put("/workspaces/{workspace}/agents/{agent}/avatar", response_model=Agent, openapi_extra=IMAGE_UPLOAD)
async def put_agent_avatar(
    request: Request, response: Response, actor: Actor, agent_id: AgentId, if_match: IfMatch
) -> Agent:
    agent = await _management(request).images.replace(
        actor=actor, agent_id=agent_id, content=await image_body(request), if_match=if_match, objects=_objects(request)
    )
    _set_etag(response, agent)
    return agent


@router.delete("/workspaces/{workspace}/agents/{agent}/avatar", response_model=Agent)
async def delete_agent_avatar(
    request: Request, response: Response, actor: Actor, agent_id: AgentId, if_match: IfMatch
) -> Agent:
    agent = await _management(request).images.replace(
        actor=actor, agent_id=agent_id, content=None, if_match=if_match, objects=_objects(request)
    )
    _set_etag(response, agent)
    return agent


@router.get("/workspaces/{workspace}/agents/{agent}/avatar/{image_id}")
async def get_agent_avatar(request: Request, actor: Actor, agent_id: AgentId, image_id: str) -> Response:
    content = await _management(request).images.read(
        actor=actor, agent_id=agent_id, image_id=image_id, objects=_objects(request)
    )
    return image_response(content)
