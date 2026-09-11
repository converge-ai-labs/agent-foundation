"""MCPConnection management and fixed OAuth protocol routes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from a13n_service.application_errors import ErrorCategory
from a13n_service.connectivity.cleanup import ConnectionCleanupReceipt
from a13n_service.etags import resource_etag
from a13n_service.http_types import IdempotencyKey
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.authentication import authenticate_mutation
from a13n_service.iam.http.resource_dependencies import WorkspaceId
from a13n_service.request_runtime import get_connectivity_control_runtime

from .domain import (
    CompleteMCPOAuthRequest,
    ConfigureMCPOAuthClientRequest,
    CreateMCPConnectionRequest,
    MCPAuthorizationLaunch,
    MCPClientMetadata,
    MCPConnection,
    MCPConnectionCollection,
    MCPConnectionCommandRequest,
    MCPOAuthClientConfiguration,
    MCPOAuthDiscovery,
    MCPToolCollection,
    ReplaceMCPCredentialsRequest,
    UpdateMCPConnectionRequest,
)
from .errors import MCPConnectionError
from .oauth_service import MCPOAuthService
from .service import MCPConnectionService

router = APIRouter(tags=["connectivity-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
IssuerKey = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]


def _connections(request: Request) -> MCPConnectionService:
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise MCPConnectionError(
            "mcp_connection_management_unavailable",
            "MCPConnection Management is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return runtime.mcp_connections


def _oauth(request: Request) -> MCPOAuthService:
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise MCPConnectionError(
            "mcp_oauth_unavailable",
            "MCP OAuth is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return runtime.mcp_oauth


def _etag(response: Response, resource: MCPConnection) -> None:
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)


@router.get("/api/v1/oauth/mcp/client-metadata/{issuer_key}.json", response_model=MCPClientMetadata)
async def mcp_client_metadata(request: Request, issuer_key: IssuerKey) -> MCPClientMetadata:
    return _oauth(request).client_metadata(issuer_key)


@router.get("/api/v1/oauth/mcp/callback/{issuer_key}", include_in_schema=False)
async def receive_mcp_oauth(
    request: Request,
    issuer_key: IssuerKey,
    state: Annotated[str, Query(min_length=32, max_length=512)],
    code: Annotated[str, Query(min_length=1, max_length=8192)],
    iss: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
) -> RedirectResponse:
    if (
        any(len(request.query_params.getlist(key)) > 1 for key in ("state", "code", "iss", "error"))
        or "error" in request.query_params
    ):
        raise MCPConnectionError(
            "invalid_oauth_response", "OAuth response is invalid.", category=ErrorCategory.invalid_request
        )
    location = await _oauth(request).receive_callback(callback_key=issuer_key, state=state, code=code, issuer=iss)
    return RedirectResponse(
        location, status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    )


@router.get("/api/v1/mcp-connections/{connection_id}/oauth-client", response_model=MCPOAuthClientConfiguration | None)
async def get_mcp_oauth_client(
    request: Request, actor: Actor, connection_id: str
) -> MCPOAuthClientConfiguration | None:
    return await _oauth(request).configuration.get(actor=actor, connection_id=connection_id)


@router.post("/api/v1/mcp-connections/{connection_id}/oauth-discovery", response_model=MCPOAuthDiscovery)
async def discover_mcp_oauth(request: Request, actor: Actor, connection_id: str) -> MCPOAuthDiscovery:
    return await _oauth(request).configuration.discover(actor=actor, connection_id=connection_id)


@router.put("/api/v1/mcp-connections/{connection_id}/oauth-client", response_model=MCPConnection)
async def configure_mcp_oauth_client(
    request: Request, response: Response, actor: Actor, connection_id: str, body: ConfigureMCPOAuthClientRequest
) -> MCPConnection:
    resource = await _oauth(request).configuration.configure(actor=actor, connection_id=connection_id, request=body)
    _etag(response, resource)
    return resource


@router.post(
    "/api/v1/workspaces/{workspace}/mcp-connections",
    response_model=MCPConnection,
    status_code=status.HTTP_201_CREATED,
)
async def create_mcp_connection(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: WorkspaceId,
    body: CreateMCPConnectionRequest,
    idempotency_key: IdempotencyKey,
) -> MCPConnection:
    resource = await _connections(request).create(
        actor=actor,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _etag(response, resource)
    return resource


@router.get("/api/v1/workspaces/{workspace}/mcp-connections", response_model=MCPConnectionCollection)
async def list_mcp_connections(
    request: Request,
    actor: Actor,
    workspace_id: WorkspaceId,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> MCPConnectionCollection:
    return await _connections(request).list(
        actor=actor,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
    )


@router.get("/api/v1/mcp-connections/{connection_id}", response_model=MCPConnection)
async def get_mcp_connection(
    request: Request,
    response: Response,
    actor: Actor,
    connection_id: str,
) -> MCPConnection:
    resource = await _connections(request).get(actor=actor, connection_id=connection_id)
    _etag(response, resource)
    return resource


@router.post("/api/v1/mcp-connections/{connection_id}/discover", response_model=MCPToolCollection)
async def discover_mcp_tools(
    request: Request, actor: Actor, connection_id: str, body: MCPConnectionCommandRequest
) -> MCPToolCollection:
    return await _connections(request).discover_tools(
        actor=actor, connection_id=connection_id, expected_version=body.expected_version
    )


@router.patch("/api/v1/mcp-connections/{connection_id}", response_model=MCPConnection)
async def update_mcp_connection(
    request: Request,
    response: Response,
    actor: Actor,
    connection_id: str,
    body: UpdateMCPConnectionRequest,
) -> MCPConnection:
    resource = await _connections(request).update(actor=actor, connection_id=connection_id, request=body)
    _etag(response, resource)
    return resource


@router.post("/api/v1/mcp-connections/{connection_id}/credentials", response_model=MCPConnection)
async def replace_mcp_credentials(
    request: Request,
    response: Response,
    actor: Actor,
    connection_id: str,
    body: ReplaceMCPCredentialsRequest,
    idempotency_key: IdempotencyKey,
) -> MCPConnection:
    resource = await _connections(request).replace_credentials(
        actor=actor,
        connection_id=connection_id,
        idempotency_key=idempotency_key,
        request=body,
    )
    _etag(response, resource)
    return resource


@router.post("/api/v1/mcp-connections/{connection_id}/authorize", response_model=MCPAuthorizationLaunch)
async def authorize_mcp_connection(
    request: Request,
    actor: Actor,
    connection_id: str,
    body: MCPConnectionCommandRequest,
    idempotency_key: IdempotencyKey,
) -> MCPAuthorizationLaunch:
    return await _oauth(request).authorize(
        actor=actor,
        connection_id=connection_id,
        idempotency_key=idempotency_key,
        expected_version=body.expected_version,
    )


@router.post("/api/v1/oauth/mcp/complete", response_model=MCPConnection)
async def complete_mcp_oauth(
    request: Request,
    response: Response,
    actor: Annotated[AuthenticatedActor, Depends(authenticate_mutation)],
    body: CompleteMCPOAuthRequest,
) -> MCPConnection:
    resource = await _oauth(request).callback(actor=actor, state=body.state, receipt=body.receipt)
    _etag(response, resource)
    return resource


@router.post("/api/v1/mcp-connections/{connection_id}/reconnect", response_model=MCPConnection)
async def reconnect_mcp_connection(
    request: Request,
    response: Response,
    actor: Actor,
    connection_id: str,
    body: MCPConnectionCommandRequest,
    idempotency_key: IdempotencyKey,
) -> MCPConnection:
    resource = await _connections(request).reconnect(
        actor=actor,
        connection_id=connection_id,
        idempotency_key=idempotency_key,
        expected_version=body.expected_version,
    )
    _etag(response, resource)
    return resource


@router.post("/api/v1/mcp-connections/{connection_id}/{action}", response_model=MCPConnection)
async def change_mcp_connection_lifecycle(
    request: Request,
    response: Response,
    actor: Actor,
    connection_id: str,
    action: Literal["enable", "disable"],
    body: MCPConnectionCommandRequest,
    idempotency_key: IdempotencyKey,
) -> MCPConnection:
    resource = await _connections(request).set_enabled(
        actor=actor,
        connection_id=connection_id,
        idempotency_key=idempotency_key,
        expected_version=body.expected_version,
        enabled=action == "enable",
    )
    _etag(response, resource)
    return resource


@router.delete("/api/v1/mcp-connections/{connection_id}", response_model=ConnectionCleanupReceipt)
async def delete_mcp_connection(
    request: Request,
    actor: Actor,
    connection_id: str,
    expected_version: Annotated[int, Query(ge=1)],
    idempotency_key: IdempotencyKey,
) -> ConnectionCleanupReceipt:
    return await _connections(request).delete(
        actor=actor,
        connection_id=connection_id,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
    )
