"""Connection management and fixed OAuth protocol routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.authentication import authenticate_mutation
from a13n_service.request_runtime import get_connectivity_control_runtime

from ..connections.domain import Connection, ConnectionCommandRequest
from .catalog import MCPServer, MCPServerCollection
from .domain import (
    ConfigureMCPOAuthClientRequest,
    MCPClientMetadata,
    MCPOAuthClientConfiguration,
    MCPOAuthDiscovery,
    MCPOAuthSetup,
    MCPOAuthSetupRequest,
    MCPToolCollection,
)
from .errors import MCPConnectionError
from .oauth_service import MCPOAuthService
from .service import MCPConnectionService

router = APIRouter(tags=["connectivity-management"])
Actor = Annotated[AuthenticatedActor, Depends(authenticate_request)]
MutationActor = Annotated[AuthenticatedActor, Depends(authenticate_mutation)]
IssuerKey = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]


def _connections(request: Request) -> MCPConnectionService:
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise MCPConnectionError(
            "connection_management_unavailable",
            "Connection Management is unavailable.",
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


@router.get("/api/v1/mcp-servers", response_model=MCPServerCollection)
async def list_mcp_servers(
    request: Request,
    actor: Actor,
    query: Annotated[str, Query(max_length=256)] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> MCPServerCollection:
    del actor
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise MCPConnectionError(
            "connection_management_unavailable",
            "Connection management is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return runtime.mcp_servers.list(query=query, limit=limit, cursor=cursor)


@router.get("/api/v1/mcp-servers/{server_key}", response_model=MCPServer)
async def get_mcp_server(request: Request, actor: Actor, server_key: str) -> MCPServer:
    del actor
    runtime = get_connectivity_control_runtime(request)
    if runtime is None:
        raise MCPConnectionError(
            "connection_management_unavailable",
            "Connection management is unavailable.",
            category=ErrorCategory.unavailable,
        )
    return runtime.mcp_servers.get(server_key)


@router.get(
    "/api/v1/oauth/mcp/client-metadata/{issuer_key}/{redirect_key}.json",
    response_model=MCPClientMetadata,
)
async def mcp_client_metadata(
    request: Request,
    issuer_key: IssuerKey,
    redirect_key: IssuerKey,
) -> MCPClientMetadata:
    return _oauth(request).client_metadata(issuer_key, redirect_key)


@router.get("/api/v1/connections/{connection_id}/mcp/oauth-client", response_model=MCPOAuthClientConfiguration | None)
async def get_mcp_oauth_client(
    request: Request, actor: Actor, connection_id: str
) -> MCPOAuthClientConfiguration | None:
    return await _oauth(request).configuration.get(actor=actor, connection_id=connection_id)


@router.post("/api/v1/connections/{connection_id}/mcp/oauth-discovery", response_model=MCPOAuthDiscovery)
async def discover_mcp_oauth(
    request: Request, actor: Actor, connection_id: str, body: MCPOAuthSetupRequest
) -> MCPOAuthDiscovery:
    return await _oauth(request).configuration.discover(actor=actor, connection_id=connection_id, request=body)


@router.post("/api/v1/connections/{connection_id}/mcp/oauth-setup", response_model=MCPOAuthSetup)
async def get_mcp_oauth_setup(
    request: Request, actor: Actor, connection_id: str, body: MCPOAuthSetupRequest
) -> MCPOAuthSetup:
    return await _oauth(request).configuration.setup(actor=actor, connection_id=connection_id, request=body)


@router.put("/api/v1/connections/{connection_id}/mcp/oauth-client", response_model=Connection)
async def configure_mcp_oauth_client(
    request: Request, response: Response, actor: MutationActor, connection_id: str, body: ConfigureMCPOAuthClientRequest
) -> Connection:
    result = await _oauth(request).configuration.configure(actor=actor, connection_id=connection_id, request=body)
    response.headers["ETag"] = resource_etag(result.id, result.updated_at)
    return result


@router.post("/api/v1/connections/{connection_id}/mcp/discover", response_model=MCPToolCollection)
async def discover_mcp_tools(
    request: Request, actor: Actor, connection_id: str, body: ConnectionCommandRequest
) -> MCPToolCollection:
    return await _connections(request).discover_tools(
        actor=actor, connection_id=connection_id, expected_version=body.expected_version
    )
