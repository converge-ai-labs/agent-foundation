"""Control-plane Memory Provider routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import resource_etag
from a13n_service.http_types import IfMatch
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.resource_dependencies import OrganizationId, WorkspaceId
from a13n_service.iam.resource_routes import require_organization_boundary
from a13n_service.openapi import ETAG_HEADERS
from a13n_service.request_runtime import get_control_runtime

from .domain import (
    CreateMemoryProviderRequest,
    MemoryProvider,
    MemoryProviderCollection,
    MemoryProviderDefinition,
    MemoryProviderDefinitionCollection,
    MemoryProviderReferenceCollection,
    UpdateMemoryProviderRequest,
)
from .providers import MemoryProviderService
from .resources import MemoryProviderError

router = APIRouter(prefix="/api/v1", tags=["memory-providers"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=2048)]


def _service(request: Request) -> MemoryProviderService:
    control = get_control_runtime(request)
    if control is None or control.memory_providers is None:
        raise MemoryProviderError(
            "memory_provider_unavailable",
            "Memory Provider management is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return control.memory_providers


@router.get("/memory-provider-types")
async def list_types(request: Request, actor: Actor) -> MemoryProviderDefinitionCollection:
    return await _service(request).type_definitions(actor=actor)


@router.get("/memory-provider-types/{provider_type}")
async def get_type(request: Request, actor: Actor, provider_type: str) -> MemoryProviderDefinition:
    catalog = await _service(request).type_definitions(actor=actor)
    for definition in catalog.items:
        if definition.type == provider_type:
            return definition
    raise MemoryProviderError(
        "memory_provider_type_not_found", "Memory Provider type not found.", category=ErrorCategory.not_found
    )


@router.get("/workspaces/{workspace}/memory-providers")
async def list_workspace_provider(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    limit: Limit = 50,
    cursor: Cursor = None,
    provider_type: Annotated[str | None, Query(alias="type", max_length=128)] = None,
    enabled: bool | None = None,
) -> MemoryProviderCollection:
    result = await _service(request).list(
        actor=actor, workspace_id=workspace_id, limit=limit, cursor=cursor, provider_type=provider_type, enabled=enabled
    )
    return result


@router.post("/workspaces/{workspace}/memory-providers", status_code=201, responses={201: {"headers": ETAG_HEADERS}})
async def create_workspace_provider(
    request: Request, actor: Actor, workspace_id: WorkspaceId, response: Response, body: CreateMemoryProviderRequest
) -> MemoryProvider:
    result = await _service(request).create(actor=actor, workspace_id=workspace_id, request=body)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.get("/workspaces/{workspace}/memory-providers/{provider_id}", responses={200: {"headers": ETAG_HEADERS}})
async def get_workspace_provider(
    request: Request, actor: Actor, workspace_id: WorkspaceId, response: Response, provider_id: str
) -> MemoryProvider:
    result = await _service(request).get(actor=actor, workspace_id=workspace_id, provider_id=provider_id)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.patch("/workspaces/{workspace}/memory-providers/{provider_id}", responses={200: {"headers": ETAG_HEADERS}})
async def update_workspace_provider(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    response: Response,
    provider_id: str,
    body: UpdateMemoryProviderRequest,
    if_match: IfMatch,
) -> MemoryProvider:
    result = await _service(request).update(
        actor=actor, workspace_id=workspace_id, provider_id=provider_id, request=body, if_match=if_match
    )
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.get("/workspaces/{workspace}/memory-providers/{provider_id}/references")
async def references_workspace_provider(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    provider_id: str,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> MemoryProviderReferenceCollection:
    result = await _service(request).references(
        actor=actor, workspace_id=workspace_id, provider_id=provider_id, limit=limit, cursor=cursor
    )
    return result


@router.get("/organizations/{organization}/memory-providers")
async def list_organization_provider(
    request: Request,
    actor: Actor,
    organization_id: OrganizationId,
    limit: Limit = 50,
    cursor: Cursor = None,
    provider_type: Annotated[str | None, Query(alias="type", max_length=128)] = None,
    enabled: bool | None = None,
) -> MemoryProviderCollection:
    require_organization_boundary(actor, organization_id)
    result = await _service(request).list(
        actor=actor, workspace_id=None, limit=limit, cursor=cursor, provider_type=provider_type, enabled=enabled
    )
    return result


@router.post(
    "/organizations/{organization}/memory-providers", status_code=201, responses={201: {"headers": ETAG_HEADERS}}
)
async def create_organization_provider(
    request: Request,
    actor: Actor,
    organization_id: OrganizationId,
    response: Response,
    body: CreateMemoryProviderRequest,
) -> MemoryProvider:
    require_organization_boundary(actor, organization_id)
    result = await _service(request).create(actor=actor, workspace_id=None, request=body)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.get("/organizations/{organization}/memory-providers/{provider_id}", responses={200: {"headers": ETAG_HEADERS}})
async def get_organization_provider(
    request: Request, actor: Actor, organization_id: OrganizationId, response: Response, provider_id: str
) -> MemoryProvider:
    require_organization_boundary(actor, organization_id)
    result = await _service(request).get(actor=actor, workspace_id=None, provider_id=provider_id)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.patch(
    "/organizations/{organization}/memory-providers/{provider_id}", responses={200: {"headers": ETAG_HEADERS}}
)
async def update_organization_provider(
    request: Request,
    actor: Actor,
    organization_id: OrganizationId,
    response: Response,
    provider_id: str,
    body: UpdateMemoryProviderRequest,
    if_match: IfMatch,
) -> MemoryProvider:
    require_organization_boundary(actor, organization_id)
    result = await _service(request).update(
        actor=actor, workspace_id=None, provider_id=provider_id, request=body, if_match=if_match
    )
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.get("/organizations/{organization}/memory-providers/{provider_id}/references")
async def references_organization_provider(
    request: Request,
    actor: Actor,
    organization_id: OrganizationId,
    provider_id: str,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> MemoryProviderReferenceCollection:
    require_organization_boundary(actor, organization_id)
    result = await _service(request).references(
        actor=actor, workspace_id=None, provider_id=provider_id, limit=limit, cursor=cursor
    )
    return result
