"""Control-plane Search Provider routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.resource_routes import require_organization_boundary
from a13n_service.request_runtime import get_control_runtime

from .adapters import SearchTransport
from .domain import (
    CreateSearchProviderRequest,
    SearchConfiguration,
    SearchProvider,
    SearchProviderCollection,
    SearchProviderDefinition,
    SearchProviderDefinitionCollection,
    SearchProviderReferenceCollection,
    SearchProviderTestResult,
    UpdateSearchProviderRequest,
)
from .probe import test_account
from .resources import SearchProviderError
from .service import SearchProviderService

router = APIRouter(prefix="/api/v1", tags=["search-providers"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


def _if_match(value: Annotated[str | None, Header(alias="If-Match", min_length=1, max_length=256)] = None) -> str:
    if value is None:
        raise SearchProviderError(
            "precondition_required", "If-Match is required.", category=ErrorCategory.precondition_required
        )
    return value


IfMatch = Annotated[str, Depends(_if_match)]
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=2048)]


def _service(request: Request) -> SearchProviderService:
    control = get_control_runtime(request)
    if control is None or control.search_providers is None:
        raise SearchProviderError(
            "search_provider_unavailable",
            "Search Provider management is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return control.search_providers


@router.get("/search-provider-types")
async def list_types(request: Request, actor: Actor) -> SearchProviderDefinitionCollection:
    return await _service(request).type_definitions(actor=actor)


@router.get("/search-provider-types/{provider_type}")
async def get_type(request: Request, actor: Actor, provider_type: str) -> SearchProviderDefinition:
    catalog = await _service(request).type_definitions(actor=actor)
    for definition in catalog.items:
        if definition.type == provider_type:
            return definition
    raise SearchProviderError(
        "search_provider_type_not_found", "Search Provider type not found.", category=ErrorCategory.not_found
    )


@router.get("/workspaces/{workspace_id}/search-providers")
async def list_workspace_provider(
    request: Request,
    actor: Actor,
    workspace_id: str,
    limit: Limit = 50,
    cursor: Cursor = None,
    provider_type: Annotated[str | None, Query(alias="type", max_length=64)] = None,
    enabled: bool | None = None,
) -> SearchProviderCollection:
    result = await _service(request).list(
        actor=actor, workspace_id=workspace_id, limit=limit, cursor=cursor, provider_type=provider_type, enabled=enabled
    )
    return result


@router.post("/workspaces/{workspace_id}/search-providers", status_code=201)
async def create_workspace_provider(
    request: Request, actor: Actor, workspace_id: str, response: Response, body: CreateSearchProviderRequest
) -> SearchProvider:
    result = await _service(request).create(actor=actor, workspace_id=workspace_id, request=body)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.get("/workspaces/{workspace_id}/search-providers/{provider_id}")
async def get_workspace_provider(
    request: Request, actor: Actor, workspace_id: str, response: Response, provider_id: str
) -> SearchProvider:
    result = await _service(request).get(actor=actor, workspace_id=workspace_id, provider_id=provider_id)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.patch("/workspaces/{workspace_id}/search-providers/{provider_id}")
async def update_workspace_provider(
    request: Request,
    actor: Actor,
    workspace_id: str,
    response: Response,
    provider_id: str,
    body: UpdateSearchProviderRequest,
    if_match: IfMatch,
) -> SearchProvider:
    result = await _service(request).update(
        actor=actor, workspace_id=workspace_id, provider_id=provider_id, request=body, if_match=if_match
    )
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.post("/workspaces/{workspace_id}/search-providers/{provider_id}/test")
async def test_workspace_provider(
    request: Request, actor: Actor, workspace_id: str, provider_id: str, body: SearchConfiguration
) -> SearchProviderTestResult:
    result = await test_account(
        _service(request), actor=actor, workspace_id=workspace_id, provider_id=provider_id, transport=SearchTransport()
    )
    return result


@router.get("/workspaces/{workspace_id}/search-providers/{provider_id}/references")
async def references_workspace_provider(
    request: Request, actor: Actor, workspace_id: str, provider_id: str, limit: Limit = 50, cursor: Cursor = None
) -> SearchProviderReferenceCollection:
    result = await _service(request).references(
        actor=actor, workspace_id=workspace_id, provider_id=provider_id, limit=limit, cursor=cursor
    )
    return result


@router.get("/organizations/{organization_id}/search-providers")
async def list_organization_provider(
    request: Request,
    actor: Actor,
    organization_id: str,
    limit: Limit = 50,
    cursor: Cursor = None,
    provider_type: Annotated[str | None, Query(alias="type", max_length=64)] = None,
    enabled: bool | None = None,
) -> SearchProviderCollection:
    require_organization_boundary(actor, organization_id)
    result = await _service(request).list(
        actor=actor, workspace_id=None, limit=limit, cursor=cursor, provider_type=provider_type, enabled=enabled
    )
    return result


@router.post("/organizations/{organization_id}/search-providers", status_code=201)
async def create_organization_provider(
    request: Request, actor: Actor, organization_id: str, response: Response, body: CreateSearchProviderRequest
) -> SearchProvider:
    require_organization_boundary(actor, organization_id)
    result = await _service(request).create(actor=actor, workspace_id=None, request=body)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.get("/organizations/{organization_id}/search-providers/{provider_id}")
async def get_organization_provider(
    request: Request, actor: Actor, organization_id: str, response: Response, provider_id: str
) -> SearchProvider:
    require_organization_boundary(actor, organization_id)
    result = await _service(request).get(actor=actor, workspace_id=None, provider_id=provider_id)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.patch("/organizations/{organization_id}/search-providers/{provider_id}")
async def update_organization_provider(
    request: Request,
    actor: Actor,
    organization_id: str,
    response: Response,
    provider_id: str,
    body: UpdateSearchProviderRequest,
    if_match: IfMatch,
) -> SearchProvider:
    require_organization_boundary(actor, organization_id)
    result = await _service(request).update(
        actor=actor, workspace_id=None, provider_id=provider_id, request=body, if_match=if_match
    )
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.post("/organizations/{organization_id}/search-providers/{provider_id}/test")
async def test_organization_provider(
    request: Request, actor: Actor, organization_id: str, provider_id: str, body: SearchConfiguration
) -> SearchProviderTestResult:
    require_organization_boundary(actor, organization_id)
    result = await test_account(
        _service(request), actor=actor, workspace_id=None, provider_id=provider_id, transport=SearchTransport()
    )
    return result


@router.get("/organizations/{organization_id}/search-providers/{provider_id}/references")
async def references_organization_provider(
    request: Request, actor: Actor, organization_id: str, provider_id: str, limit: Limit = 50, cursor: Cursor = None
) -> SearchProviderReferenceCollection:
    require_organization_boundary(actor, organization_id)
    result = await _service(request).references(
        actor=actor, workspace_id=None, provider_id=provider_id, limit=limit, cursor=cursor
    )
    return result
