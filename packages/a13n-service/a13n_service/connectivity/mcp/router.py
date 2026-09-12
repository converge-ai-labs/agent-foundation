"""Connection management and fixed OAuth protocol routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import RedirectResponse

from a13n_service.application_errors import ErrorCategory
from a13n_service.etags import resource_etag
from a13n_service.iam import AuthenticatedActor, authenticate_request
from a13n_service.iam.http.authentication import authenticate_mutation
from a13n_service.request_runtime import get_connectivity_control_runtime

from ..connections.domain import Connection, ConnectionCommandRequest
from .domain import (
    ConfigureMCPOAuthClientRequest,
    MCPClientMetadata,
    MCPOAuthClientConfiguration,
    MCPOAuthDiscovery,
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


@router.get("/api/v1/connections/{connection_id}/mcp/oauth-client", response_model=MCPOAuthClientConfiguration | None)
async def get_mcp_oauth_client(
    request: Request, actor: Actor, connection_id: str
) -> MCPOAuthClientConfiguration | None:
    return await _oauth(request).configuration.get(actor=actor, connection_id=connection_id)


@router.post("/api/v1/connections/{connection_id}/mcp/oauth-discovery", response_model=MCPOAuthDiscovery)
async def discover_mcp_oauth(request: Request, actor: Actor, connection_id: str) -> MCPOAuthDiscovery:
    return await _oauth(request).configuration.discover(actor=actor, connection_id=connection_id)


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
