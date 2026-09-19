"""ConnectorProvider management and verified setup callback routes."""

from __future__ import annotations

from typing import Annotated, Literal

from a13n_harness.providers.connector.contracts import ConnectorToolPage
from fastapi import APIRouter, Depends, Query, Request, Response, status

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import resource_etag
from a13n_service.http_types import IdempotencyKey
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.resource_dependencies import OrganizationId, WorkspaceId
from a13n_service.iam.resource_routes import require_organization_boundary
from a13n_service.provider_metadata import ProviderMetadataCollection
from a13n_service.request_runtime import get_connectivity_control_runtime

from .domain import (
    Connector,
    ConnectorCollection,
    ConnectorProvider,
    ConnectorProviderCollection,
    ConnectorProviderCommandRequest,
    ConnectorProviderMetadata,
    ConnectorProviderStatus,
    ConnectorProviderTestResult,
    CreateConnectorProviderRequest,
    ReplaceConnectorProviderCredentialsRequest,
    UpdateConnectorProviderRequest,
)
from .errors import ConnectorError
from .service import ConnectorProviderService

router = APIRouter(prefix="/api/v1", tags=["connectivity-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]


def _connector_providers(request: Request) -> ConnectorProviderService:
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise ConnectorError(
            "connector_provider_management_unavailable",
            "ConnectorProvider Management is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return runtime.connector_providers


def _etag(response: Response, resource: ConnectorProvider) -> None:
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)


@router.get("/connector-provider-types")
async def list_connector_provider_types(
    request: Request, actor: Actor
) -> ProviderMetadataCollection[ConnectorProviderMetadata]:
    return await _connector_providers(request).provider_types(actor=actor)


@router.get("/connector-provider-types/{provider_type}")
async def get_connector_provider_type(request: Request, actor: Actor, provider_type: str) -> ConnectorProviderMetadata:
    return await _connector_providers(request).provider_type(actor=actor, provider_type=provider_type)


@router.get("/connector-providers/{connector_provider_id}/connectors/{connector_key}", response_model=Connector)
async def get_connector(request: Request, actor: Actor, connector_provider_id: str, connector_key: str) -> Connector:
    return await _connector_providers(request).discover_connector(
        actor=actor, connector_provider_id=connector_provider_id, connector_key=connector_key
    )


@router.get(
    "/connector-providers/{connector_provider_id}/connectors/{connector_key}/tools",
    response_model=ConnectorToolPage,
)
async def preview_connector_tools(
    request: Request, actor: Actor, connector_provider_id: str, connector_key: str
) -> ConnectorToolPage:
    return await _connector_providers(request).preview_tools(
        actor=actor, connector_provider_id=connector_provider_id, connector_key=connector_key
    )


@router.post("/connector-providers/{connector_provider_id}/discover-connectors", response_model=ConnectorCollection)
async def discover_connectors(
    request: Request,
    actor: Actor,
    connector_provider_id: str,
    query: str = Query(default="", max_length=256),
    cursor: str | None = Query(default=None, max_length=2048),
    limit: int = Query(default=100, ge=1, le=200),
    refresh: bool = False,
) -> ConnectorCollection:
    return await _connector_providers(request).discover_connectors(
        actor=actor,
        connector_provider_id=connector_provider_id,
        query=query,
        cursor=cursor,
        limit=limit,
        refresh=refresh,
    )


@router.post(
    "/workspaces/{workspace}/connector-providers",
    response_model=ConnectorProvider,
    status_code=status.HTTP_201_CREATED,
)
async def create_connector_provider(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: WorkspaceId,
    body: CreateConnectorProviderRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorProvider:
    resource = await _connector_providers(request).create(
        actor=actor,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _etag(response, resource)
    return resource


@router.get("/workspaces/{workspace}/connector-providers", response_model=ConnectorProviderCollection)
async def list_connector_providers(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> ConnectorProviderCollection:
    return await _connector_providers(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/connector-providers/{connector_provider_id}", response_model=ConnectorProvider)
async def get_connector_provider(
    request: Request,
    response: Response,
    actor: Actor,
    connector_provider_id: str,
) -> ConnectorProvider:
    resource = await _connector_providers(request).get(actor=actor, connector_provider_id=connector_provider_id)
    _etag(response, resource)
    return resource


@router.patch("/connector-providers/{connector_provider_id}", response_model=ConnectorProvider)
async def update_connector_provider(
    request: Request,
    response: Response,
    actor: Actor,
    connector_provider_id: str,
    body: UpdateConnectorProviderRequest,
) -> ConnectorProvider:
    resource = await _connector_providers(request).update(
        actor=actor, connector_provider_id=connector_provider_id, request=body
    )
    _etag(response, resource)
    return resource


@router.post("/connector-providers/{connector_provider_id}/credentials", response_model=ConnectorProvider)
async def replace_connector_provider_credentials(
    request: Request,
    response: Response,
    actor: Actor,
    connector_provider_id: str,
    body: ReplaceConnectorProviderCredentialsRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorProvider:
    resource = await _connector_providers(request).replace_credentials(
        actor=actor,
        connector_provider_id=connector_provider_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _etag(response, resource)
    return resource


@router.post("/connector-providers/{connector_provider_id}/test", response_model=ConnectorProviderTestResult)
async def test_connector_provider(
    request: Request,
    actor: Actor,
    connector_provider_id: str,
    body: ConnectorProviderCommandRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorProviderTestResult:
    return await _connector_providers(request).test(
        actor=actor,
        connector_provider_id=connector_provider_id,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
    )


@router.post("/connector-providers/{connector_provider_id}/{action}", response_model=ConnectorProvider)
async def change_connector_provider_lifecycle(
    request: Request,
    response: Response,
    actor: Actor,
    connector_provider_id: str,
    action: Literal["enable", "disable"],
    body: ConnectorProviderCommandRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorProvider:
    resource = await _connector_providers(request).set_status(
        actor=actor,
        connector_provider_id=connector_provider_id,
        status=ConnectorProviderStatus.active if action == "enable" else ConnectorProviderStatus.disabled,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
    )
    _etag(response, resource)
    return resource


# Register this catch-all after named commands so it cannot shadow them.


@router.post(
    "/organizations/{organization}/connector-providers",
    response_model=ConnectorProvider,
    status_code=status.HTTP_201_CREATED,
)
async def organization_create_connector_provider(
    request: Request,
    response: Response,
    actor: Actor,
    organization_id: OrganizationId,
    body: CreateConnectorProviderRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorProvider:
    require_organization_boundary(actor, organization_id)
    resource = await _connector_providers(request).create(
        actor=actor,
        workspace_id=None,
        idempotency_key=idempotency_key,
        request=body,
    )
    _etag(response, resource)
    return resource


@router.get("/organizations/{organization}/connector-providers", response_model=ConnectorProviderCollection)
async def organization_list_connector_providers(
    request: Request,
    actor: Actor,
    organization_id: OrganizationId,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> ConnectorProviderCollection:
    require_organization_boundary(actor, organization_id)
    return await _connector_providers(request).list(
        actor=actor,
        workspace_id=None,
        limit=limit,
        cursor=cursor,
    )
