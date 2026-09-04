"""MCPConnection management and fixed OAuth protocol routes."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.request_runtime import get_connectivity_control_runtime, get_process_runtime

from .domain import (
    CreateMCPConnectionRequest,
    MCPAuthorizationLaunch,
    MCPClientMetadata,
    MCPConnection,
    MCPConnectionCollection,
    MCPConnectionCommandRequest,
    ReplaceMCPCredentialsRequest,
    UpdateMCPConnectionRequest,
)
from .errors import MCPConnectionError
from .oauth_service import MCPOAuthService
from .service import MCPConnectionService

router = APIRouter(tags=["connectivity-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=512)]


def _connections(request: Request) -> MCPConnectionService:
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise MCPConnectionError(
            "mcp_connection_management_unavailable",
            "MCPConnection Management is unavailable.",
            status_code=503,
        )
    return runtime.mcp_connections


def _oauth(request: Request) -> MCPOAuthService:
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise MCPConnectionError(
            "mcp_oauth_unavailable",
            "MCP OAuth is unavailable.",
            status_code=503,
        )
    return runtime.mcp_oauth


def _etag(response: Response, resource: MCPConnection) -> None:
    response.headers["ETag"] = resource_etag(resource.id, resource.updated_at)


@router.get("/api/v1/oauth/mcp/client-metadata.json", response_model=MCPClientMetadata)
async def mcp_client_metadata(request: Request) -> MCPClientMetadata:
    oauth = _oauth(request)
    runtime = get_process_runtime(request)
    if runtime is None:
        raise MCPConnectionError("mcp_oauth_unavailable", "MCP OAuth is unavailable.", status_code=503)
    return MCPClientMetadata(
        client_id=oauth.client_metadata_url,
        client_name=runtime.settings.connectivity_oauth_client_name,
        redirect_uris=(oauth.redirect_uri,),
    )


@router.post(
    "/api/v1/workspaces/{workspace_id}/mcp-connections",
    response_model=MCPConnection,
    status_code=status.HTTP_201_CREATED,
)
async def create_mcp_connection(
    request: Request,
    response: Response,
    actor: Actor,
    workspace_id: str,
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


@router.get("/api/v1/workspaces/{workspace_id}/mcp-connections", response_model=MCPConnectionCollection)
async def list_mcp_connections(
    request: Request,
    actor: Actor,
    workspace_id: str,
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


@router.get("/api/v1/oauth/mcp/callback", response_model=MCPConnection)
async def mcp_oauth_callback(
    request: Request,
    response: Response,
    actor: Actor,
    code: Annotated[str, Query(min_length=1, max_length=8192)],
    state_value: Annotated[str, Query(alias="state", min_length=32, max_length=512)],
    issuer: Annotated[str, Query(alias="iss", min_length=1, max_length=2048)],
) -> MCPConnection:
    resource = await _oauth(request).callback(actor=actor, state=state_value, code=code, issuer=issuer)
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


@router.delete("/api/v1/mcp-connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_connection(
    request: Request,
    actor: Actor,
    connection_id: str,
    expected_version: Annotated[int, Query(ge=1)],
    idempotency_key: IdempotencyKey,
) -> Response:
    await _connections(request).delete(
        actor=actor,
        connection_id=connection_id,
        idempotency_key=idempotency_key,
        expected_version=expected_version,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
