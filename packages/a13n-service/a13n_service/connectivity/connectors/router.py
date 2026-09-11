"""ConnectorProvider management and verified setup callback routes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response, status

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.cleanup import ConnectionCleanupReceipt
from a13n_service.etags import resource_etag
from a13n_service.http_types import IdempotencyKey
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.resource_dependencies import OrganizationId, WorkspaceId
from a13n_service.iam.resource_routes import require_organization_boundary
from a13n_service.request_runtime import get_connectivity_control_runtime

from .connections import ConnectorConnectionService
from .contracts import ConnectorToolPage
from .domain import (
    CompleteConnectorSetupRequest,
    Connector,
    ConnectorCollection,
    ConnectorConnection,
    ConnectorConnectionCollection,
    ConnectorConnectionCommandRequest,
    ConnectorProvider,
    ConnectorProviderCollection,
    ConnectorProviderCommandRequest,
    ConnectorProviderStatus,
    ConnectorProviderTestResult,
    ConnectorSetupCompletion,
    ConnectorSetupLaunch,
    CreateConnectorConnectionRequest,
    CreateConnectorProviderRequest,
    ReconnectConnectorConnectionRequest,
    ReplaceConnectorProviderCredentialsRequest,
    StartConnectorConnectionSetupRequest,
    UpdateConnectorConnectionRequest,
    UpdateConnectorProviderRequest,
)
from .errors import ConnectorError
from .registry import ConnectorProviderDefinitionCollection
from .service import ConnectorProviderService

router = APIRouter(tags=["connectivity-management"])
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


def _connections(request: Request) -> ConnectorConnectionService:
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise ConnectorError(
            "connector_provider_management_unavailable",
            "ConnectorProvider Management is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return runtime.connector_connections


def _etag(response: Response, resource: ConnectorProvider | ConnectorConnection) -> None:
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)


@router.get("/api/v1/connector-provider-types", response_model=ConnectorProviderDefinitionCollection)
async def list_connector_provider_types(request: Request, actor: Actor) -> ConnectorProviderDefinitionCollection:
    return await _connector_providers(request).type_definitions(actor=actor)


@router.get("/api/v1/connector-providers/{connector_provider_id}/connectors/{connector_key}", response_model=Connector)
async def get_connector(request: Request, actor: Actor, connector_provider_id: str, connector_key: str) -> Connector:
    return await _connector_providers(request).discover_connector(
        actor=actor, connector_provider_id=connector_provider_id, connector_key=connector_key
    )


@router.get(
    "/api/v1/connector-providers/{connector_provider_id}/connectors/{connector_key}/tools",
    response_model=ConnectorToolPage,
)
async def preview_connector_tools(
    request: Request, actor: Actor, connector_provider_id: str, connector_key: str
) -> ConnectorToolPage:
    return await _connector_providers(request).preview_tools(
        actor=actor, connector_provider_id=connector_provider_id, connector_key=connector_key
    )


@router.post(
    "/api/v1/connector-providers/{connector_provider_id}/discover-connectors", response_model=ConnectorCollection
)
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
    "/api/v1/workspaces/{workspace}/connector-providers",
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


@router.get("/api/v1/workspaces/{workspace}/connector-providers", response_model=ConnectorProviderCollection)
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


@router.get("/api/v1/connector-providers/{connector_provider_id}", response_model=ConnectorProvider)
async def get_connector_provider(
    request: Request,
    response: Response,
    actor: Actor,
    connector_provider_id: str,
) -> ConnectorProvider:
    resource = await _connector_providers(request).get(actor=actor, connector_provider_id=connector_provider_id)
    _etag(response, resource)
    return resource


@router.patch("/api/v1/connector-providers/{connector_provider_id}", response_model=ConnectorProvider)
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


@router.post("/api/v1/connector-providers/{connector_provider_id}/credentials", response_model=ConnectorProvider)
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


@router.post("/api/v1/connector-providers/{connector_provider_id}/test", response_model=ConnectorProviderTestResult)
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


@router.post("/api/v1/connector-providers/{connector_provider_id}/{action}", response_model=ConnectorProvider)
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


@router.post(
    "/api/v1/workspaces/{workspace}/connector-connections",
    response_model=ConnectorConnection,
    status_code=status.HTTP_201_CREATED,
)
async def create_connector_connection(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    body: CreateConnectorConnectionRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorConnection:
    return await _connections(request).create(
        actor=actor,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        request=body,
    )


@router.post(
    "/api/v1/connector-connections/{connection_id}/setup",
    response_model=ConnectorSetupLaunch,
    status_code=status.HTTP_200_OK,
)
async def start_connector_connection_setup(
    request: Request,
    actor: Actor,
    connection_id: str,
    body: StartConnectorConnectionSetupRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorSetupLaunch:
    return await _connections(request).start_setup(
        actor=actor,
        connection_id=connection_id,
        idempotency_key=idempotency_key,
        expected_version=body.expected_version,
        setup=body.setup,
        return_path=body.return_path,
        browser_nonce=body.browser_nonce,
    )


@router.get(
    "/api/v1/workspaces/{workspace}/connector-connections",
    response_model=ConnectorConnectionCollection,
)
async def list_connector_connections(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> ConnectorConnectionCollection:
    return await _connections(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/api/v1/connector-connections/{connection_id}", response_model=ConnectorConnection)
async def get_connector_connection(
    request: Request,
    response: Response,
    actor: Actor,
    connection_id: str,
) -> ConnectorConnection:
    resource = await _connections(request).get(actor=actor, connection_id=connection_id)
    _etag(response, resource)
    return resource


@router.patch("/api/v1/connector-connections/{connection_id}", response_model=ConnectorConnection)
async def update_connector_connection(
    request: Request,
    response: Response,
    actor: Actor,
    connection_id: str,
    body: UpdateConnectorConnectionRequest,
) -> ConnectorConnection:
    resource = await _connections(request).update(
        actor=actor,
        connection_id=connection_id,
        request=body,
    )
    _etag(response, resource)
    return resource


@router.post(
    "/api/v1/connector-connections/{connection_id}/reconnect",
    response_model=ConnectorSetupLaunch,
)
async def reconnect_connector_connection(
    request: Request,
    actor: Actor,
    connection_id: str,
    body: ReconnectConnectorConnectionRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorSetupLaunch:
    return await _connections(request).reconnect(
        actor=actor,
        connection_id=connection_id,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
        setup=body.setup,
        return_path=body.return_path,
        browser_nonce=body.browser_nonce,
    )


@router.post(
    "/api/v1/connector-connections/{connection_id}/revoke",
    response_model=ConnectionCleanupReceipt,
    status_code=status.HTTP_200_OK,
)
async def revoke_connector_connection(
    request: Request,
    actor: Actor,
    connection_id: str,
    body: ConnectorConnectionCommandRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectionCleanupReceipt:
    return await _connections(request).revoke(
        actor=actor,
        connection_id=connection_id,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
    )


# Register this catch-all after named commands so it cannot shadow them.
@router.post(
    "/api/v1/connector-connections/{connection_id}/{action}",
    response_model=ConnectorConnection,
)
async def change_connector_connection_lifecycle(
    request: Request,
    response: Response,
    actor: Actor,
    connection_id: str,
    action: Literal["enable", "disable"],
    body: ConnectorConnectionCommandRequest,
    idempotency_key: IdempotencyKey,
) -> ConnectorConnection:
    resource = await _connections(request).set_enabled(
        actor=actor,
        connection_id=connection_id,
        enabled=action == "enable",
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
    )
    _etag(response, resource)
    return resource


@router.delete(
    "/api/v1/connector-connections/{connection_id}",
    response_model=ConnectionCleanupReceipt,
)
async def delete_connector_connection(
    request: Request,
    actor: Actor,
    connection_id: str,
    expected_version: Annotated[int, Query(ge=1)],
    idempotency_key: IdempotencyKey,
) -> ConnectionCleanupReceipt:
    return await _connections(request).delete(
        actor=actor,
        connection_id=connection_id,
        expected_version=expected_version,
        idempotency_key=idempotency_key,
    )


@router.post("/api/v1/connector-setup/complete", response_model=ConnectorSetupCompletion)
async def complete_connector_setup(
    request: Request,
    body: CompleteConnectorSetupRequest,
    actor: Actor,
) -> ConnectorSetupCompletion:
    return_path = await _connections(request).complete_callback(
        actor=actor,
        attempt_id=body.attempt_id,
        browser_nonce=body.browser_nonce,
        session_uri=body.session_uri,
    )
    return ConnectorSetupCompletion(return_path=return_path)


@router.post(
    "/api/v1/organizations/{organization}/connector-providers",
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


@router.get("/api/v1/organizations/{organization}/connector-providers", response_model=ConnectorProviderCollection)
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
