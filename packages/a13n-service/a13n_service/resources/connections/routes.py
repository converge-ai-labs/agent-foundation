"""Workspace connections, their authorization and tools, the public authorization callback, and the Remote MCP
servers suggested for new connections."""

import secrets
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from a13n_service.infra.http import IfMatch, PageLimit, tagged
from a13n_service.providers.tools import mcp_catalog
from a13n_service.resources.connections import authorization, discovery, service
from a13n_service.resources.connections.authorization import CALLBACK_PATH, Callback, callback_url, flow_cookie
from a13n_service.resources.connections.schemas import (
    AuthorizationRequest,
    AuthorizationResult,
    CallbackOutcome,
    Connection,
    ConnectionCreate,
    ConnectionPage,
    ConnectionTest,
    ConnectionUpdate,
    OAuthRedirect,
    RevokedConnection,
    ToolPage,
)
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.tenancy.requests import Actor, limit_guessing

router = APIRouter(prefix="/api/v1", tags=["connections"])
COLLECTION = "/workspaces/{workspace_id}/connections"
ITEM = COLLECTION + "/{connection_id}"


@router.post(COLLECTION, response_model=Connection, status_code=201)
async def create_connection(
    response: Response, workspace_id: str, body: ConnectionCreate, actor: Actor, runtime: CurrentRuntime
) -> Connection:
    result = await service.create_connection(
        runtime.storage,
        actor,
        workspace_id,
        body,
        keys=runtime.keys,
        registry=runtime.registry,
        policy=runtime.endpoint_policy,
    )
    return tagged(response, result)


@router.get(COLLECTION, response_model=ConnectionPage)
async def list_connections(
    workspace_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> ConnectionPage:
    return await service.list_connections(runtime.storage, actor, workspace_id, limit=limit, cursor=cursor)


@router.get(ITEM, response_model=Connection)
async def get_connection(
    response: Response, workspace_id: str, connection_id: str, actor: Actor, runtime: CurrentRuntime
) -> Connection:
    return tagged(response, await service.get_connection(runtime.storage, actor, workspace_id, connection_id))


@router.patch(ITEM, response_model=Connection)
async def update_connection(
    response: Response,
    workspace_id: str,
    connection_id: str,
    body: ConnectionUpdate,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> Connection:
    result = await service.update_connection(
        runtime.storage,
        actor,
        workspace_id,
        connection_id,
        body,
        if_match=if_match,
        keys=runtime.keys,
        registry=runtime.registry,
        policy=runtime.endpoint_policy,
    )
    return tagged(response, result)


@router.post(ITEM + "/test", response_model=ConnectionTest)
async def test_connection(
    workspace_id: str, connection_id: str, actor: Actor, runtime: CurrentRuntime
) -> ConnectionTest:
    return await discovery.test_connection(
        runtime.storage,
        actor,
        workspace_id,
        connection_id,
        redis=runtime.redis,
        keys=runtime.keys,
        registry=runtime.registry,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    )


@router.get(ITEM + "/tools", response_model=ToolPage)
async def list_tools(workspace_id: str, connection_id: str, actor: Actor, runtime: CurrentRuntime) -> ToolPage:
    return await discovery.list_tools(
        runtime.storage,
        actor,
        workspace_id,
        connection_id,
        redis=runtime.redis,
        keys=runtime.keys,
        registry=runtime.registry,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    )


@router.post(ITEM + "/authorize", response_model=AuthorizationResult)
async def authorize_connection(
    response: Response,
    workspace_id: str,
    connection_id: str,
    body: AuthorizationRequest,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> AuthorizationResult:
    """A browser flow needs a login session and is bound to this browser by a cookie the callback checks; an API
    key authorizes only a client-credentials client, without a browser."""
    browser = secrets.token_urlsafe(32)
    result = await authorization.authorize_connection(
        runtime.storage,
        actor,
        workspace_id,
        connection_id,
        body,
        browser=browser,
        if_match=if_match,
        keys=runtime.keys,
        registry=runtime.registry,
        policy=runtime.endpoint_policy,
        settings=runtime.settings,
    )
    if result.redirect_url is not None:
        response.set_cookie(
            flow_cookie(connection_id, runtime.settings),
            browser,
            max_age=runtime.settings.providers.flow_seconds,
            path=CALLBACK_PATH,
            secure=runtime.settings.server.https,
            httponly=True,
            samesite="lax",
        )
    return result


@router.post(ITEM + "/revoke", response_model=RevokedConnection)
async def revoke_connection(
    response: Response,
    workspace_id: str,
    connection_id: str,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> RevokedConnection:
    result = await authorization.revoke_connection(
        runtime.storage,
        actor,
        workspace_id,
        connection_id,
        if_match=if_match,
        keys=runtime.keys,
        registry=runtime.registry,
        policy=runtime.endpoint_policy,
        settings=runtime.settings,
    )
    return tagged(response, result)


@router.get("/connections/redirect-uri", response_model=OAuthRedirect)
async def get_redirect_uri(actor: Actor, runtime: CurrentRuntime) -> OAuthRedirect:
    """The deployment's callback, for registering an OAuth client in advance; the same for every connection."""
    return OAuthRedirect(redirect_uri=callback_url(runtime.settings))


@router.get("/mcp-servers", response_model=mcp_catalog.McpServerPage)
async def list_mcp_servers(
    actor: Actor,
    runtime: CurrentRuntime,
    query: Annotated[str | None, Query(max_length=128)] = None,
    limit: PageLimit = 50,
    cursor: str | None = None,
) -> mcp_catalog.McpServerPage:
    """Suggested Remote MCP servers, packaged and from the deployment; readable by every signed-in principal."""
    return mcp_catalog.list_mcp_servers(runtime.settings.providers.mcp_servers, query=query, limit=limit, cursor=cursor)


@router.get(
    CALLBACK_PATH.removeprefix("/api/v1"),
    response_model=CallbackOutcome,
    responses={303: {"description": "Back to the return URL the authorization named"}},
)
async def complete_authorization(
    request: Request,
    runtime: CurrentRuntime,
    state: Annotated[str, Query(min_length=16, max_length=256)],
    code: Annotated[str | None, Query(max_length=4096)] = None,
    error: Annotated[str | None, Query(max_length=256)] = None,
    iss: Annotated[str | None, Query(max_length=2048)] = None,
    session_uri: Annotated[str | None, Query(max_length=2048)] = None,
) -> Response:
    """Public: the one-use state authenticates the browser that the authorization server sends back, and the flow
    cookie the browser that started the flow.

    Attempts per client address share the login flows' bound.
    """
    await limit_guessing(request, "connection_callback")
    result = await authorization.complete_authorization(
        runtime.storage,
        runtime.access,
        Callback(state=state, code=code, error=error, iss=iss, session_uri=session_uri),
        browsers=request.cookies,
        keys=runtime.keys,
        registry=runtime.registry,
        policy=runtime.endpoint_policy,
        settings=runtime.settings,
    )
    headers = {"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}
    outcome = result.outcome
    answer: Response
    if result.return_url is None:
        answer = JSONResponse(outcome.model_dump(mode="json"), headers=headers)
    else:
        query = urlencode({key: value for key, value in outcome.model_dump().items() if value is not None})
        separator = "&" if "?" in result.return_url else "?"
        answer = RedirectResponse(f"{result.return_url}{separator}{query}", status_code=303, headers=headers)
    answer.delete_cookie(
        flow_cookie(outcome.connection_id, runtime.settings),
        path=CALLBACK_PATH,
        secure=runtime.settings.server.https,
        httponly=True,
        samesite="lax",
    )
    return answer
