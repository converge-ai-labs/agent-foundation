"""Connections: write-only credentials, preconditions, authorization, caller headers, MCP tools and OAuth."""

import asyncio
import base64
import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx2
import pytest
import uvicorn
from a13n_harness import AgentSpec, DefinitionError, HarnessBuilder, RunBindings
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.tools import ToolDispatch
from a13n_service.providers.tools.oauth import OAuthClient, OAuthError, renew_access
from a13n_service.resources.connections.credentials import AccountFlow, HeadersSecret, OAuthTokens, protect, reveal
from a13n_service.resources.connections.operations import claim_operation, recover_operations
from a13n_service.resources.connections.runtime import open_connections, resolve_connections
from a13n_service.resources.connections.schemas import ConnectionCreate, ConnectionSelection, ConnectionUpdate
from a13n_service.resources.connections.service import (
    create_connection,
    get_connection,
    update_connection,
    validate_caller_headers,
    validate_selection,
)
from a13n_service.resources.connections.tables import ConnectionRow
from a13n_service.settings import Settings
from a13n_service.tenancy.access import principal_for
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal, WorkspaceScope, execution_authority
from a13n_service.tenancy.tables import WorkspaceRow
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, RetryPromptPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from sqlalchemy import select

pytestmark = pytest.mark.anyio

SECRET = "mcp-header-secret"
CLIENT_SECRET = "oauth-client-secret"
RETURN_URL = "https://console.test/connections/done"
CALLBACK = "http://127.0.0.1:8000/api/v1/connections/callback"
CALLBACK_PATH = "/api/v1/connections/callback"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    providers = settings.providers.model_copy(update={"return_urls": (RETURN_URL,)})
    return settings.model_copy(update={"providers": providers})


def etag(resource: dict) -> str:
    return f'"{resource["id"]}:{resource["version"]}"'


@asynccontextmanager
async def serve(app: Any) -> AsyncIterator[str]:
    """A local HTTP server on an ephemeral port for the duration of the context."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        await task


class RemoteServer:
    """An MCP server (`echo`, `ping`) behind an OAuth 2.1 authorization server with DCR, PKCE and rotation.

    With `client_secret`, token requests must authenticate the client, which may also use `client_credentials`.
    Dynamic registration answers `client-1`, or with `distinct_clients` a new client each time.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        oauth: bool = False,
        client_secret: str | None = None,
        extra_tools: int = 0,
        distinct_clients: bool = False,
    ) -> None:
        self.url = ""
        self.distinct_clients = distinct_clients
        # The client IDs token requests may name: the one registered in advance and each one registered since.
        self.clients = {"client-1"}
        self.requests: list[dict[str, str]] = []
        self.echoed: list[str] = []
        self.required = token is not None or oauth
        self.tokens = {token} if token else set()
        self.refresh_tokens: set[str] = set()
        self.codes: dict[str, str] = {}
        self.revoked: list[str] = []
        self.issued = 0
        self.client_secret = client_secret
        # Each token request's grant and how the client authenticated: `basic`, `post` or `none`.
        self.grants: list[tuple[str, str]] = []
        # When set, renewals (refresh or client-credentials grants) answer this status and OAuth error.
        self.refusal: tuple[int, str] | None = None
        self.revocation_status = 200
        mcp = FastMCP("remote")

        @mcp.tool
        def echo(text: str) -> str:
            self.echoed.append(text)
            return text

        @mcp.tool(annotations={"readOnlyHint": True})
        def ping() -> str:
            return "pong"

        for index in range(extra_tools):
            mcp.tool(name=f"tool_{index:03}")(ping)

        inner = mcp.http_app(path="/mcp")
        app = FastAPI(lifespan=inner.lifespan)
        app.get("/.well-known/oauth-protected-resource/mcp")(self._resource)
        app.get("/.well-known/oauth-authorization-server")(self._metadata)
        app.post("/register", status_code=201)(self._register)
        app.post("/token")(self._token)
        app.post("/revoke")(self._revoke)
        app.mount("/", inner)
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            headers = {name.decode(): value.decode() for name, value in scope["headers"]}
            self.requests.append(headers)
            if self.required and headers.get("authorization") not in {f"Bearer {token}" for token in self.tokens}:
                await JSONResponse({"error": "invalid_token"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def grant(self, authorize_url: str) -> tuple[str, str]:
        """The user approves in the browser: the state to return and a code bound to the PKCE challenge."""
        query = {name: values[0] for name, values in parse_qs(urlsplit(authorize_url).query).items()}
        assert query["code_challenge_method"] == "S256" and query["redirect_uri"] == CALLBACK
        code = f"code-{len(self.codes)}"
        self.codes[code] = query["code_challenge"]
        return query["state"], code

    def _resource(self) -> dict:
        return {"resource": f"{self.url}/mcp", "authorization_servers": [self.url], "scopes_supported": ["tools"]}

    def _metadata(self) -> dict:
        return {
            "issuer": self.url,
            "authorization_endpoint": f"{self.url}/authorize",
            "token_endpoint": f"{self.url}/token",
            "registration_endpoint": f"{self.url}/register",
            "revocation_endpoint": f"{self.url}/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_basic", "client_secret_post"],
            "authorization_response_iss_parameter_supported": True,
        }

    async def _register(self, request: Request) -> dict:
        client_id = f"client-{len(self.clients) + 1}" if self.distinct_clients else "client-1"
        self.clients.add(client_id)
        return {**await request.json(), "client_id": client_id}

    async def _token(self, request: Request) -> JSONResponse:
        form = {name: values[0] for name, values in parse_qs((await request.body()).decode()).items()}
        assert form["client_id"] in self.clients and form["resource"] == f"{self.url}/mcp"
        method, secret = "none", None
        if request.headers.get("authorization", "").startswith("Basic "):
            method, secret = "basic", base64.b64decode(request.headers["authorization"][6:]).decode().split(":")[1]
        elif "client_secret" in form:
            method, secret = "post", form["client_secret"]
        grant = form["grant_type"]
        self.grants.append((grant, method))
        if secret != self.client_secret or (grant == "client_credentials" and secret is None):
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        if self.refusal is not None and grant != "authorization_code":
            status, error = self.refusal
            return JSONResponse({"error": error}, status_code=status)
        if grant == "authorization_code":
            digest = hashlib.sha256(form["code_verifier"].encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if self.codes.pop(form["code"], None) != challenge or form["redirect_uri"] != CALLBACK:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
        elif grant == "refresh_token" and form["refresh_token"] in self.refresh_tokens:
            self.refresh_tokens.remove(form["refresh_token"])
        elif grant != "client_credentials":
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        self.issued += 1
        access, refresh = f"access-{self.issued}", f"refresh-{self.issued}"
        self.tokens.add(access)
        issued = {"access_token": access, "token_type": "Bearer", "expires_in": 3600}
        if grant != "client_credentials":
            self.refresh_tokens.add(refresh)
            issued["refresh_token"] = refresh
        return JSONResponse(issued)

    async def _revoke(self, request: Request) -> JSONResponse:
        self.revoked.append(parse_qs((await request.body()).decode())["token"][0])
        return JSONResponse({}, status_code=self.revocation_status)


@asynccontextmanager
async def remote(**options: Any) -> AsyncIterator[RemoteServer]:
    server = RemoteServer(**options)
    async with serve(server) as url:
        server.url = url
        yield server


async def post(service, path: str, body: dict | None = None, *, status: int = 200, **headers: str) -> dict:  # type: ignore[no-untyped-def]
    response = await service.client.post(service.api + path, json=body, headers=headers)
    assert response.status_code == status, response.text
    return response.json()


async def create(service, body: dict) -> dict:  # type: ignore[no-untyped-def]
    created = await post(service, "/connections", {"type": "mcp", "name": "Remote", **body}, status=201)
    assert created["version"] == 1
    return created


async def api_key(service) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """The administrator's workspace API key, as the header that presents it."""
    issued = await service.client.post(
        "/api/v1/users/me/keys", json={"name": "automation", "workspace_id": service.tenant.workspace_id}
    )
    assert issued.status_code == 201, issued.text
    return {"authorization": f"Bearer {issued.json()['secret']}"}


def admin(service) -> Principal:  # type: ignore[no-untyped-def]
    return Principal(
        service.tenant.principal_id, "user", (Grant(service.tenant.organization_id, None, BUILT_IN_ROLES["admin"]),)
    )


def scope(service) -> WorkspaceScope:  # type: ignore[no-untyped-def]
    return WorkspaceScope(service.tenant.organization_id, service.tenant.workspace_id)


async def row(service, connection_id: str) -> ConnectionRow:  # type: ignore[no-untyped-def]
    async with short_session(service.runtime.storage) as session:
        found = await session.get(ConnectionRow, connection_id)
    assert found is not None
    return found


async def view(service, connection_id: str) -> dict:  # type: ignore[no-untyped-def]
    response = await service.client.get(f"{service.api}/connections/{connection_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def tokens(service, connection_id: str) -> OAuthTokens:  # type: ignore[no-untyped-def]
    stored = await row(service, connection_id)
    assert stored.tokens is not None
    return reveal(service.runtime.keys, stored.organization_id, stored.id, "tokens", stored.tokens, OAuthTokens)


async def expire_soon(service, connection_id: str) -> None:  # type: ignore[no-untyped-def]
    """The access token expires within the renewal margin, so the next use renews it."""
    async with transaction(service.runtime.storage) as session:
        locked = await session.get(ConnectionRow, connection_id, with_for_update=True)
        assert locked is not None
        locked.expires_at = await now(session) + timedelta(seconds=5)


async def authorized(service, server: "RemoteServer", connection: dict) -> dict:  # type: ignore[no-untyped-def]
    """The OAuth connection after its initiator's browser completed a new authorization."""
    item = f"/connections/{connection['id']}"
    redirect = await post(service, item + "/authorize", {"return_url": RETURN_URL}, **{"If-Match": etag(connection)})
    state, code = server.grant(redirect["redirect_url"])
    response = await service.client.get(CALLBACK_PATH, params={"state": state, "code": code, "iss": server.url})
    assert response.status_code == 303, response.text
    assert parse_qs(urlsplit(response.headers["location"]).query)["status"] == ["ready"]
    return await view(service, connection["id"])


async def patch(service, connection: dict, body: dict) -> dict:  # type: ignore[no-untyped-def]
    response = await service.client.patch(
        f"{service.api}/connections/{connection['id']}", json=body, headers={"If-Match": etag(connection)}
    )
    assert response.status_code == 200, response.text
    return response.json()


def calling(tool: str, arguments: dict, seen: dict[str, list[str]]) -> FunctionModel:
    """A model that calls `tool` once, then answers with what the call returned."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        results = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart | RetryPromptPart)
        ]
        if not results:
            seen["tools"] = sorted(definition.name for definition in info.function_tools)
            yield {0: DeltaToolCall(name=tool, json_args=json.dumps(arguments), tool_call_id="call-1")}
            return
        seen["results"] = [str(part.content) for part in results]
        yield "done"

    return FunctionModel(stream_function=stream)


async def run_tool(capabilities: Sequence[AbstractCapability], tool: str, arguments: dict) -> dict[str, list[str]]:
    seen: dict[str, list[str]] = {}
    executable = HarnessBuilder().build(
        AgentSpec(), output_type=str, model=calling(tool, arguments, seen), capabilities=tuple(capabilities)
    )
    result = await executable.run("go", bindings=RunBindings.embedded())
    assert result.output_or_raise() == "done"
    return seen


@asynccontextmanager
async def opened(service, selections: list[ConnectionSelection], mcp_headers: dict, dispatches: list[ToolDispatch]):  # type: ignore[no-untyped-def]
    runtime = service.runtime

    async def check(dispatch: ToolDispatch) -> None:
        dispatches.append(dispatch)

    async with short_session(runtime.storage) as session:
        actor = await principal_for(session, service.runtime.access, service.tenant.principal_id)
        selected = await resolve_connections(
            session,
            actor,
            scope(service),
            selections,
            mcp_headers,
            authority=execution_authority(actor, scope(service)),
        )
    async with open_connections(
        selected,
        check,
        storage=runtime.storage,
        redis=runtime.redis,
        keys=runtime.keys,
        registry=runtime.registry,
        policy=runtime.endpoint_policy,
        settings=runtime.settings.providers,
    ) as capabilities:
        yield capabilities


async def test_entered_credentials_are_write_only_and_changes_need_the_current_etag(service) -> None:  # type: ignore[no-untyped-def]
    created = await create(
        service,
        {
            "config": {"url": "http://127.0.0.1:9/mcp", "headers": ["x-api-key"]},
            "auth": "headers",
            "credential": {"headers": {"X-API-Key": SECRET}},
        },
    )
    assert created["status"] == "ready" and created["credential_configured"] and created["enabled"]
    item = f"/connections/{created['id']}"
    for path in (item, "/connections"):
        response = await service.client.get(service.api + path)
        assert response.status_code == 200 and SECRET not in response.text
    stored = await row(service, created["id"])
    assert stored.credential is not None and SECRET not in json.dumps(stored.credential)
    secret = reveal(
        service.runtime.keys, stored.organization_id, stored.id, "credential", stored.credential, HeadersSecret
    )
    assert secret.headers == {"x-api-key": SECRET}

    response = await service.client.patch(service.api + item, json={"name": "Renamed"})
    assert response.status_code == 428
    response = await service.client.patch(service.api + item, json={"name": "Renamed"}, headers={"If-Match": '"x"'})
    assert response.status_code == 412
    response = await service.client.patch(
        service.api + item, json={"name": "Renamed"}, headers={"If-Match": etag(created)}
    )
    assert response.status_code == 200 and response.headers["etag"] == etag(response.json())
    renamed = response.json()
    assert renamed["name"] == "Renamed" and renamed["credential_configured"]
    # A change to nothing keeps the version and records no event.
    response = await service.client.patch(
        service.api + item, json={"name": "Renamed"}, headers={"If-Match": etag(renamed)}
    )
    assert response.status_code == 200 and response.json()["version"] == renamed["version"]

    # Another server must not receive this server's credential.
    moved = {"config": {"url": "http://127.0.0.2:9/mcp", "headers": ["x-api-key"]}}
    response = await service.client.patch(service.api + item, json=moved, headers={"If-Match": etag(renamed)})
    assert response.status_code == 200, response.text
    moved = response.json()
    assert moved["status"] == "pending" and not moved["credential_configured"]

    disabled = await patch(service, moved, {"enabled": False})
    assert disabled["enabled"] is False and disabled["version"] == moved["version"] + 1
    enabled = await patch(service, disabled, {"enabled": True})
    assert enabled["enabled"] is True
    for retired in ("/enable", "/disable"):
        response = await service.client.post(service.api + item + retired, headers={"If-Match": etag(enabled)})
        assert response.status_code == 404
    async with short_session(service.runtime.storage) as session:
        events = (
            await session.execute(
                select(AuditEventRow.action, AuditEventRow.details).where(AuditEventRow.target_id == created["id"])
            )
        ).all()
    assert sorted((action, details.get("fields")) for action, details in events) == [
        ("connection.create", None),
        ("connection.update", ["config"]),
        ("connection.update", ["enabled"]),
        ("connection.update", ["enabled"]),
        ("connection.update", ["name"]),
    ]


async def test_configuration_and_credentials_must_agree(service) -> None:  # type: ignore[no-untyped-def]
    url = "http://127.0.0.1:9/mcp"
    refused = [
        {"config": {"url": url}, "auth": "headers", "credential": {"headers": {"x-api-key": SECRET}}},
        {"config": {"url": url, "headers": ["x-api-key"]}, "auth": "headers", "credential": {"token": SECRET}},
        {"config": {"url": url}, "auth": "none", "credential": {"token": SECRET}},
        {"config": {"url": url, "oauth": {}}, "auth": "bearer", "credential": {"token": SECRET}},
        {"config": {"url": url}, "auth": "account"},
        # Headers the transport owns never become credential headers that could never be sent.
        {"config": {"url": url, "headers": ["host"]}, "auth": "headers", "credential": {"headers": {"host": SECRET}}},
        {"config": {"url": url, "headers": ["mcp-session-id"]}, "auth": "headers"},
        {"config": {"url": "http://10.1.2.3/mcp"}},
        {"config": {"app": "github", "actions": ["GITHUB_CREATE"]}},
    ]
    for body in refused:
        response = await service.client.post(
            service.api + "/connections", json={"type": "mcp", "name": "Remote", **body}
        )
        assert response.status_code == 400, body
        assert SECRET not in response.text
    bearer = await create(service, {"config": {"url": url}, "auth": "bearer", "credential": {"token": SECRET}})
    assert bearer["status"] == "ready"
    oauth = await create(service, {"config": {"url": url, "oauth": {"scopes": ["tools"]}}, "auth": "oauth"})
    assert oauth["status"] == "pending" and not oauth["credential_configured"]


async def test_connections_follow_workspace_grants(service) -> None:  # type: ignore[no-untyped-def]
    runtime = service.runtime
    storage, organization_id, workspace_id = (
        runtime.storage,
        service.tenant.organization_id,
        service.tenant.workspace_id,
    )
    context = {"keys": runtime.keys, "registry": runtime.registry, "policy": runtime.endpoint_policy}
    created = await create(service, {"config": {"url": "http://127.0.0.1:9/mcp"}})
    other = new_object_id("ws")
    async with transaction(storage) as session:
        session.add(WorkspaceRow(id=other, organization_id=organization_id, name="Second"))

    def member(workspace: str, role: str) -> Principal:
        return Principal(
            service.tenant.principal_id, "user", (Grant(organization_id, workspace, BUILT_IN_ROLES[role]),)
        )

    body = ConnectionCreate(type="mcp", name="Remote", config={"url": "http://127.0.0.1:9/mcp"})  # type: ignore[arg-type]
    viewer = member(workspace_id, "viewer")
    assert (await get_connection(storage, viewer, workspace_id, created["id"])).id == created["id"]
    with pytest.raises(ServiceError, match="cannot perform"):
        await create_connection(storage, viewer, workspace_id, body, **context)
    with pytest.raises(ServiceError, match="cannot perform"):
        await update_connection(
            storage, viewer, workspace_id, created["id"], ConnectionUpdate(name="x"), if_match=etag(created), **context
        )
    outsider = member(other, "admin")
    with pytest.raises(ServiceError, match="cannot perform"):
        await get_connection(storage, outsider, workspace_id, created["id"])
    # A connection is reached only through its own workspace.
    with pytest.raises(ServiceError, match="not found"):
        await get_connection(storage, outsider, other, created["id"])
    assert (await create_connection(storage, member(workspace_id, "builder"), workspace_id, body, **context)).enabled


async def test_caller_headers_and_selections_name_usable_connections(service) -> None:  # type: ignore[no-untyped-def]
    config = {"url": "http://127.0.0.1:9/mcp", "headers": ["x-api-key"], "tools": ["echo"]}
    created = await create(
        service, {"config": config, "auth": "headers", "credential": {"headers": {"x-api-key": SECRET}}}
    )
    workspace_id, connection_id = service.tenant.workspace_id, created["id"]
    async with short_session(service.runtime.storage) as session:
        await validate_caller_headers(session, workspace_id, {connection_id: {"x-trace": "t1"}})
        selected = ConnectionSelection(connection_id=connection_id)
        assert await validate_selection(session, admin(service), scope(service), selected) == "mcp"
        refusals: list[tuple[str, Any]] = [
            ("collide", validate_caller_headers(session, workspace_id, {connection_id: {"x-api-key": "other"}})),
            ("not an enabled", validate_caller_headers(session, workspace_id, {"conn_missing": {"x-trace": "1"}})),
            (
                "does not expose",
                validate_selection(
                    session,
                    admin(service),
                    scope(service),
                    ConnectionSelection(connection_id=connection_id, tools=("ping",)),
                ),
            ),
        ]
        for message, refusal in refusals:
            with pytest.raises(ServiceError, match=message):
                await refusal
    await patch(service, created, {"enabled": False})
    async with short_session(service.runtime.storage) as session:
        with pytest.raises(ServiceError, match="not an enabled"):
            await validate_caller_headers(session, workspace_id, {connection_id: {"x-trace": "t1"}})
        with pytest.raises(ServiceError, match="disabled"):
            await validate_selection(session, admin(service), scope(service), selected)


async def test_mcp_tools_are_discovered_and_called_with_credential_and_caller_headers(service) -> None:  # type: ignore[no-untyped-def]
    async with remote() as server:
        created = await create(
            service,
            {
                "config": {"url": f"{server.url}/mcp", "headers": ["x-api-key"], "tools": ["echo"]},
                "auth": "headers",
                "credential": {"headers": {"x-api-key": SECRET}},
            },
        )
        item = f"/connections/{created['id']}"
        tested = await post(service, item + "/test")
        assert tested["status"] == "succeeded", tested
        assert sorted(tool["name"] for tool in tested["tools"]) == ["echo", "ping"]
        listed = (await service.client.get(service.api + item + "/tools")).json()
        assert [tool["name"] for tool in listed["items"]] == [tool["name"] for tool in tested["tools"]]
        assert all(request.get("x-api-key") == SECRET for request in server.requests)

        dispatches: list[ToolDispatch] = []
        selection = ConnectionSelection(connection_id=created["id"])
        async with opened(service, [selection], {created["id"]: {"x-trace": "t1"}}, dispatches) as capabilities:
            seen = await run_tool(capabilities, "echo", {"text": "hello"})
        assert seen["tools"] == ["echo"]
        assert seen["results"] == ["hello"] and server.echoed == ["hello"]
        assert dispatches == [ToolDispatch(created["id"], None, "echo", "call-1")]
        assert server.requests[-1]["x-trace"] == "t1" and server.requests[-1]["x-api-key"] == SECRET

        # A deferred selection shows the model a loader instead of the server's tool definitions.
        deferred = ConnectionSelection(connection_id=created["id"], defer_loading=True)
        async with opened(service, [deferred], {}, dispatches) as capabilities:
            seen = await run_tool(capabilities, "echo", {"text": "unloaded"})
        assert seen["tools"] == ["load_capability"] and server.echoed == ["hello"]

        # A connection disabled during the run refuses the call before it is sent.
        async with opened(service, [selection], {}, dispatches) as capabilities:
            await patch(service, created, {"enabled": False})
            seen = await run_tool(capabilities, "echo", {"text": "again"})
        assert server.echoed == ["hello"] and len(dispatches) == 1
        assert "disabled" in seen["results"][0]


async def test_oauth_authorization_code_flow_with_pkce_refresh_and_revocation(service) -> None:  # type: ignore[no-untyped-def]
    async with remote(oauth=True) as server:
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        item = f"/connections/{created['id']}"
        refused = await post(service, item + "/test")
        assert refused["status"] == "failed"
        response = await service.client.post(
            service.api + item + "/authorize", json={"return_url": "https://evil.test"}
        )
        assert response.status_code == 400
        redirect = await post(service, item + "/authorize", {"return_url": RETURN_URL}, **{"If-Match": etag(created)})
        assert parse_qs(urlsplit(redirect["redirect_url"]).query)["scope"] == ["tools"]
        state, code = server.grant(redirect["redirect_url"])
        assert (await service.client.get(service.api + item)).json()["authorization_pending"] is True

        response = await service.client.get("/api/v1/connections/callback", params={"state": "x" * 43, "code": code})
        assert response.status_code == 400
        callback = {"state": state, "code": code, "iss": server.url}
        response = await service.client.get("/api/v1/connections/callback", params=callback)
        assert response.status_code == 303, response.text
        assert response.headers["referrer-policy"] == "no-referrer"
        target = urlsplit(response.headers["location"])
        assert f"{target.scheme}://{target.netloc}{target.path}" == RETURN_URL
        assert parse_qs(target.query) == {"connection_id": [created["id"]], "status": ["ready"]}
        # The state is used once.
        assert (await service.client.get("/api/v1/connections/callback", params=callback)).status_code == 400

        connection = (await service.client.get(service.api + item)).json()
        assert connection["status"] == "ready" and connection["credential_configured"]
        assert not connection["authorization_pending"]
        stored = await row(service, created["id"])
        assert stored.tokens is not None and "access-1" not in json.dumps([stored.credential, stored.tokens])
        tested = await post(service, item + "/test")
        assert tested["status"] == "succeeded", tested
        assert server.requests[-1]["authorization"] == "Bearer access-1"

        # An access token about to expire is refreshed once, and the rotated refresh token replaces the old one.
        async with transaction(service.runtime.storage) as session:
            locked = await session.get(ConnectionRow, created["id"])
            assert locked is not None
            locked.expires_at = await now(session) + timedelta(seconds=5)
        assert (await post(service, item + "/test"))["status"] == "succeeded"
        assert server.issued == 2 and server.requests[-1]["authorization"] == "Bearer access-2"
        assert (await tokens(service, created["id"])).refresh_token == "refresh-2"

        current = (await service.client.get(service.api + item)).json()
        revoked = await post(service, item + "/revoke", **{"If-Match": etag(current)})
        assert revoked["status"] == "pending" and not revoked["credential_configured"]
        assert revoked["remote_revocation"] == "revoked" and server.revoked == ["refresh-2"]

        # A refused authorization returns to the Console with its error and leaves the connection unchanged.
        redirect = await post(service, item + "/authorize", {"return_url": RETURN_URL}, **{"If-Match": etag(revoked)})
        state, _ = server.grant(redirect["redirect_url"])
        response = await service.client.get(
            "/api/v1/connections/callback", params={"state": state, "error": "access_denied"}
        )
        assert response.status_code == 303
        assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == ["access_denied"]
        failed = (await service.client.get(service.api + item)).json()
        assert failed["status"] == "pending" and not failed["authorization_pending"]
        assert failed["failure"]["reason"] == "rejected" and failed["failure"]["code"] == "access_denied"


async def test_the_public_callback_bounds_state_guessing_per_client(service) -> None:  # type: ignore[no-untyped-def]
    limit = service.runtime.settings.auth.login_limit
    guess = {"state": "x" * 43, "code": "guess"}
    statuses = [
        (await service.client.get("/api/v1/connections/callback", params=guess)).status_code for _ in range(limit + 1)
    ]
    assert statuses == [400] * limit + [429]


class HeldRefresh(RemoteServer):
    """The authorization server holds each refresh until released, recording the refresh token presented."""

    def __init__(self) -> None:
        super().__init__(oauth=True)
        self.arrived, self.release = asyncio.Event(), asyncio.Event()
        self.presented: list[str] = []

    async def _token(self, request: Request) -> JSONResponse:
        form = parse_qs((await request.body()).decode())
        if form["grant_type"] == ["refresh_token"]:
            self.presented.append(form["refresh_token"][0])
            self.arrived.set()
            await self.release.wait()
        return await super()._token(request)


async def test_starting_an_authorization_keeps_the_working_credential(service) -> None:  # type: ignore[no-untyped-def]
    """Only a completed flow replaces the credential: a refresh in flight when a flow starts finishes and is kept,
    whether or not the flow is ever completed."""
    server = HeldRefresh()
    async with serve(server) as url:
        server.url = url
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        connection = await authorized(service, server, created)
        item = f"/connections/{created['id']}"
        await expire_soon(service, created["id"])

        refreshing = asyncio.create_task(post(service, item + "/test"))
        await server.arrived.wait()
        redirect = await post(
            service, item + "/authorize", {"return_url": RETURN_URL}, **{"If-Match": etag(connection)}
        )
        server.release.set()
        assert (await refreshing)["status"] == "succeeded"

        kept = await view(service, created["id"])
        assert kept["status"] == "ready" and kept["credential_configured"] and kept["failure"] is None
        assert kept["authorization_pending"]
        assert (await tokens(service, created["id"])).refresh_token == "refresh-2"
        assert (await post(service, item + "/test"))["status"] == "succeeded"
        assert server.requests[-1]["authorization"] == "Bearer access-2"

        state, code = server.grant(redirect["redirect_url"])
        callback = {"state": state, "code": code, "iss": server.url}
        assert (await service.client.get(CALLBACK_PATH, params=callback)).status_code == 303
        assert (await tokens(service, created["id"])).refresh_token == "refresh-3"
        assert server.presented == ["refresh-1"]


async def test_tools_show_their_schemas_and_hints_and_a_test_is_recorded_without_a_new_etag(service) -> None:  # type: ignore[no-untyped-def]
    async with remote() as server:
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}})
        assert created["last_test"] is None
        item = f"/connections/{created['id']}"
        tested = await post(service, item + "/test")
        ping = next(tool for tool in tested["tools"] if tool["name"] == "ping")
        assert ping["annotations"] == {"readOnlyHint": True}
        assert ping["output_schema"]["properties"] == {"result": {"type": "string"}}
        response = await service.client.get(service.api + item)
        connection = response.json()
        assert response.headers["etag"] == etag(created) and connection["updated_at"] == created["updated_at"]
        recorded = {field: tested[field] for field in ("connection_version", "status", "message", "tested_at")}
        assert connection["last_test"] == recorded and recorded["status"] == "succeeded"

    # The server is gone: the failed test replaces the outcome, with safe text only.
    failed = await post(service, item + "/test")
    assert failed["status"] == "failed" and failed["message"] and failed["tools"] == []
    assert (await service.client.get(service.api + item)).json()["last_test"]["status"] == "failed"

    # An outcome for a later version is never replaced by a test of an earlier one.
    async with transaction(service.runtime.storage) as session:
        locked = await session.get(ConnectionRow, created["id"])
        assert locked is not None and locked.last_test is not None
        locked.last_test = {**locked.last_test, "connection_version": created["version"] + 1}
    await post(service, item + "/test")
    kept = (await service.client.get(service.api + item)).json()["last_test"]
    assert kept["connection_version"] == created["version"] + 1


async def test_a_connection_selects_from_a_server_larger_than_it_may_expose(service) -> None:  # type: ignore[no-untyped-def]
    async with remote(extra_tools=130) as server:
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}})
        item = f"/connections/{created['id']}"
        tested = await post(service, item + "/test")
        assert tested["status"] == "succeeded" and len(tested["tools"]) == 132

        # Without any selection the connection would expose every listed tool; a run refuses that.
        dispatches: list[ToolDispatch] = []
        everything = ConnectionSelection(connection_id=created["id"])
        async with opened(service, [everything], {}, dispatches) as capabilities:
            with pytest.raises(DefinitionError, match="more than 128 tools"):
                await run_tool(capabilities, "echo", {"text": "unsent"})
        # An agent's selection narrows the listing first.
        agent_subset = ConnectionSelection(connection_id=created["id"], tools=("echo", "tool_129"))
        async with opened(service, [agent_subset], {}, dispatches) as capabilities:
            seen = await run_tool(capabilities, "echo", {"text": "hello"})
        assert seen["tools"] == ["echo", "tool_129"] and server.echoed == ["hello"]

        # So does the connection's own selection.
        config = {"url": f"{server.url}/mcp", "tools": ["ping", "tool_000"]}
        response = await service.client.patch(
            service.api + item, json={"config": config}, headers={"If-Match": etag(created)}
        )
        assert response.status_code == 200, response.text
        async with opened(service, [everything], {}, dispatches) as capabilities:
            seen = await run_tool(capabilities, "ping", {})
        assert seen["tools"] == ["ping", "tool_000"] and seen["results"] == ["pong"]
        assert [dispatch.tool_name for dispatch in dispatches] == ["echo", "ping"]


async def test_oauth_client_settings_and_secret_must_agree(service) -> None:  # type: ignore[no-untyped-def]
    url = "http://127.0.0.1:9/mcp"
    registered = {"client_id": "client-1", "token_endpoint_auth_method": "client_secret_post"}
    refused = [
        {"config": {"url": url, "oauth": {"token_endpoint_auth_method": "client_secret_post"}}, "auth": "oauth"},
        {"config": {"url": url, "oauth": {"client_id": "c", "grant_type": "client_credentials"}}, "auth": "oauth"},
        {"config": {"url": url, "oauth": {"client_id": "c"}}, "auth": "oauth", "client_secret": CLIENT_SECRET},
        {"config": {"url": url}, "auth": "bearer", "credential": {"token": SECRET}, "client_secret": CLIENT_SECRET},
    ]
    for body in refused:
        response = await service.client.post(
            service.api + "/connections", json={"type": "mcp", "name": "Remote", **body}
        )
        assert response.status_code == 400, body
        assert CLIENT_SECRET not in response.text
    # A client that authenticates with a secret cannot authorize before one is entered.
    created = await create(service, {"config": {"url": url, "oauth": registered}, "auth": "oauth"})
    assert not created["client_secret_configured"]
    response = await service.client.post(
        service.api + f"/connections/{created['id']}/authorize", json={}, headers={"If-Match": etag(created)}
    )
    assert response.status_code == 409 and response.json()["error"]["details"]["reason"] == "client_secret_missing"
    # Clients registered in advance allow the deployment's callback.
    response = await service.client.get("/api/v1/connections/redirect-uri")
    assert response.status_code == 200 and response.json() == {"redirect_uri": CALLBACK}


async def test_a_confidential_client_authenticates_with_its_write_only_secret(service) -> None:  # type: ignore[no-untyped-def]
    async with remote(oauth=True, client_secret=CLIENT_SECRET) as server:
        oauth = {"client_id": "client-1", "token_endpoint_auth_method": "client_secret_basic"}
        config = {"url": f"{server.url}/mcp", "oauth": oauth}
        created = await create(service, {"config": config, "auth": "oauth", "client_secret": CLIENT_SECRET})
        assert created["client_secret_configured"] and not created["credential_configured"]
        item = f"/connections/{created['id']}"
        stored = await row(service, created["id"])
        assert stored.client_secret is not None and CLIENT_SECRET not in json.dumps(stored.client_secret)

        redirect = await post(service, item + "/authorize", {"return_url": RETURN_URL}, **{"If-Match": etag(created)})
        assert parse_qs(urlsplit(redirect["redirect_url"]).query)["client_id"] == ["client-1"]
        state, code = server.grant(redirect["redirect_url"])
        callback = {"state": state, "code": code, "iss": server.url}
        assert (await service.client.get("/api/v1/connections/callback", params=callback)).status_code == 303
        assert (await service.client.get(service.api + item)).json()["status"] == "ready"
        async with transaction(service.runtime.storage) as session:
            locked = await session.get(ConnectionRow, created["id"])
            assert locked is not None
            locked.expires_at = await now(session) + timedelta(seconds=5)
        assert (await post(service, item + "/test"))["status"] == "succeeded"
        assert server.grants == [("authorization_code", "basic"), ("refresh_token", "basic")]

        # Other scopes keep the client and its secret; another client ID drops the secret.
        current = (await service.client.get(service.api + item)).json()
        rescoped = {"config": {**config, "oauth": {**oauth, "scopes": ["tools"]}}}
        response = await service.client.patch(service.api + item, json=rescoped, headers={"If-Match": etag(current)})
        assert response.status_code == 200, response.text
        current = response.json()
        assert current["client_secret_configured"] and not current["credential_configured"]
        moved = {"config": {**config, "oauth": {**oauth, "client_id": "client-2"}}}
        response = await service.client.patch(service.api + item, json=moved, headers={"If-Match": etag(current)})
        assert response.status_code == 200 and not response.json()["client_secret_configured"]
        assert (await row(service, created["id"])).client_secret is None

    async with short_session(service.runtime.storage) as session:
        events = (
            await session.scalars(select(AuditEventRow.details).where(AuditEventRow.target_id == created["id"]))
        ).all()
    assert events and CLIENT_SECRET not in json.dumps(events)


async def test_a_client_credentials_grant_authorizes_a_machine_account_without_a_browser(service) -> None:  # type: ignore[no-untyped-def]
    async with remote(oauth=True, client_secret=CLIENT_SECRET) as server:
        oauth = {
            "client_id": "client-1",
            "token_endpoint_auth_method": "client_secret_post",
            "grant_type": "client_credentials",
        }
        body = {"config": {"url": f"{server.url}/mcp", "oauth": oauth}, "auth": "oauth", "client_secret": CLIENT_SECRET}
        created = await create(service, body)
        item = f"/connections/{created['id']}"
        # No browser takes part, so an API key may authorize it.
        authorized = await post(
            service, item + "/authorize", {}, **{"If-Match": etag(created)}, **await api_key(service)
        )
        assert authorized == {"redirect_url": None, "expires_at": None}
        connection = (await service.client.get(service.api + item)).json()
        assert connection["status"] == "ready" and connection["credential_configured"]
        assert not connection["authorization_pending"] and connection["failure"] is None
        assert (await post(service, item + "/test"))["status"] == "succeeded"
        assert server.requests[-1]["authorization"] == "Bearer access-1"

        # An expiring token is renewed by asking again: there is no refresh token.
        async with transaction(service.runtime.storage) as session:
            locked = await session.get(ConnectionRow, created["id"])
            assert locked is not None
            locked.expires_at = await now(session) + timedelta(seconds=5)
        assert (await post(service, item + "/test"))["status"] == "succeeded"
        assert server.requests[-1]["authorization"] == "Bearer access-2"
        assert server.grants == [("client_credentials", "post"), ("client_credentials", "post")]

        # A new secret drops the tokens obtained with the old one; a rejected one is recorded, never echoed.
        current = (await service.client.get(service.api + item)).json()
        response = await service.client.patch(
            service.api + item, json={"client_secret": "wrong-secret"}, headers={"If-Match": etag(current)}
        )
        assert response.status_code == 200, response.text
        current = response.json()
        assert current["status"] == "pending" and not current["credential_configured"]
        response = await service.client.post(
            service.api + item + "/authorize", json={}, headers={"If-Match": etag(current)}
        )
        assert response.status_code == 503 and "wrong-secret" not in response.text
        failed = (await service.client.get(service.api + item)).json()
        assert failed["status"] == "pending" and failed["failure"]["code"] == "invalid_client"

        # Revoking ends the machine account's token and keeps the client's secret for a later authorization.
        response = await service.client.patch(
            service.api + item, json={"client_secret": CLIENT_SECRET}, headers={"If-Match": etag(failed)}
        )
        await post(service, item + "/authorize", {}, **{"If-Match": etag(response.json())})
        current = (await service.client.get(service.api + item)).json()
        revoked = await post(service, item + "/revoke", **{"If-Match": etag(current)})
        assert revoked["status"] == "pending" and revoked["client_secret_configured"]
        assert server.revoked == ["access-3"]


async def test_the_sweep_fails_an_operation_whose_owner_vanished(service) -> None:  # type: ignore[no-untyped-def]
    async with remote(oauth=True) as server:
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        await authorized(service, server, created)
    storage = service.runtime.storage
    async with transaction(storage) as session:
        locked = await session.get(ConnectionRow, created["id"], with_for_update=True)
        assert locked is not None
        claim_operation(locked, "refresh", current=await now(session) - timedelta(seconds=60), seconds=5)
    assert await recover_operations(storage, limit=10) == 1
    recovered = await view(service, created["id"])
    assert recovered["status"] == "reauthorization_required" and not recovered["credential_configured"]
    assert recovered["failure"]["reason"] == "outcome_unknown"
    assert recovered["failure"]["code"] == "deadline_exceeded"
    assert await recover_operations(storage, limit=10) == 0


async def test_a_refresh_whose_owner_vanished_is_failed_before_its_refresh_token_is_presented_again(  # type: ignore[no-untyped-def]
    service,
) -> None:
    """The owner may have rotated the refresh token before it crashed, so the next user never sends it again."""
    server = HeldRefresh()
    server.release.set()
    async with serve(server) as url:
        server.url = url
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        await authorized(service, server, created)
        async with transaction(service.runtime.storage) as session:
            locked = await session.get(ConnectionRow, created["id"], with_for_update=True)
            assert locked is not None
            current = await now(session)
            locked.expires_at = current + timedelta(seconds=5)
            claim_operation(locked, "refresh", current=current - timedelta(seconds=60), seconds=5)

        assert (await post(service, f"/connections/{created['id']}/test"))["status"] == "failed"
        lost = await view(service, created["id"])
        assert lost["status"] == "reauthorization_required" and not lost["credential_configured"]
        assert lost["failure"]["operation_kind"] == "refresh" and lost["failure"]["code"] == "deadline_exceeded"
        assert server.presented == []
    async with short_session(service.runtime.storage) as session:
        actions = (
            await session.scalars(select(AuditEventRow.action).where(AuditEventRow.target_id == created["id"]))
        ).all()
    assert "connection.operation.expire" in actions


async def test_a_cancelled_caller_never_loses_the_rotated_credential(service) -> None:  # type: ignore[no-untyped-def]
    """The renewal and its publication finish shielded; the caller's cancellation arrives afterwards."""
    server = HeldRefresh()
    async with serve(server) as url:
        server.url = url
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        connection = await authorized(service, server, created)
        await expire_soon(service, created["id"])
        async with anyio.create_task_group() as group:
            group.start_soon(post, service, f"/connections/{created['id']}/test")
            await server.arrived.wait()
            group.cancel_scope.cancel()
            server.release.set()

        renewed = await view(service, created["id"])
        assert renewed["status"] == "ready" and renewed["failure"] is None
        assert renewed["version"] == connection["version"]
        assert (await tokens(service, created["id"])).refresh_token == "refresh-2"
        assert (await post(service, f"/connections/{created['id']}/test"))["status"] == "succeeded"
        assert server.requests[-1]["authorization"] == "Bearer access-2" and server.presented == ["refresh-1"]


async def test_only_a_lost_or_refused_grant_clears_the_credential(service) -> None:  # type: ignore[no-untyped-def]
    async with remote(oauth=True) as server:
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        connection = await authorized(service, server, created)
        item = f"/connections/{created['id']}/test"

        # A refusal that never touched the grant keeps the credential; a later use renews again.
        server.refusal = (400, "temporarily_unavailable")
        await expire_soon(service, created["id"])
        assert (await post(service, item))["status"] == "failed"
        kept = await view(service, created["id"])
        assert kept["status"] == "ready" and kept["credential_configured"]
        assert kept["failure"]["reason"] == "rejected" and kept["failure"]["code"] == "temporarily_unavailable"
        assert kept["version"] == connection["version"]
        server.refusal = None
        assert (await post(service, item))["status"] == "succeeded"
        assert (await tokens(service, created["id"])).refresh_token == "refresh-2"
        assert (await view(service, created["id"]))["failure"] is None

        # A refused grant is gone.
        server.refusal = (400, "invalid_grant")
        await expire_soon(service, created["id"])
        assert (await post(service, item))["status"] == "failed"
        lost = await view(service, created["id"])
        assert lost["status"] == "reauthorization_required" and not lost["credential_configured"]
        assert lost["failure"]["code"] == "invalid_grant"


async def move_token_endpoint(service, connection_id: str, url: str) -> None:  # type: ignore[no-untyped-def]
    """The stored credential's client now asks for tokens at `url`."""
    keys = service.runtime.keys
    async with transaction(service.runtime.storage) as session:
        locked = await session.get(ConnectionRow, connection_id, with_for_update=True)
        assert locked is not None and locked.credential is not None
        client = reveal(keys, locked.organization_id, locked.id, "credential", locked.credential, OAuthClient)
        moved = client.model_copy(update={"token_endpoint": url})
        locked.credential = protect(keys, locked.organization_id, locked.id, "credential", moved)


async def test_a_refresh_the_endpoint_policy_refuses_keeps_the_credential(service) -> None:  # type: ignore[no-untyped-def]
    """A refused destination is never dialed, so the grant is intact once the operator allows it again."""
    async with remote(oauth=True) as server:
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        await authorized(service, server, created)
        item = f"/connections/{created['id']}/test"
        await move_token_endpoint(service, created["id"], "http://169.254.169.254/token")
        await expire_soon(service, created["id"])
        assert (await post(service, item))["status"] == "failed"
        kept = await view(service, created["id"])
        assert kept["status"] == "ready" and kept["credential_configured"]
        assert kept["failure"]["reason"] == "rejected" and kept["failure"]["code"] == "token_endpoint_denied"

        await move_token_endpoint(service, created["id"], f"{server.url}/token")
        assert (await post(service, item))["status"] == "succeeded"
        assert (await tokens(service, created["id"])).refresh_token == "refresh-2"


async def test_a_refresh_that_never_reached_the_token_endpoint_keeps_the_credential(  # type: ignore[no-untyped-def]
    serve, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Connecting times out well inside the operation's bound, so an unreachable authorization server is known
    never to have received the refresh token."""
    resolve = anyio.getaddrinfo

    async def stalled(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host == "token.test":
            await anyio.sleep_forever()
        return await resolve(host, *args, **kwargs)

    providers = settings.providers.model_copy(update={"operation_seconds": 2})
    async with (
        serve(settings=settings.model_copy(update={"providers": providers})) as service,
        remote(oauth=True) as server,
    ):
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        await authorized(service, server, created)
        await move_token_endpoint(service, created["id"], "http://token.test/token")
        monkeypatch.setattr(anyio, "getaddrinfo", stalled)
        await expire_soon(service, created["id"])
        assert (await post(service, f"/connections/{created['id']}/test"))["status"] == "failed"
        kept = await view(service, created["id"])
        assert kept["status"] == "ready" and kept["credential_configured"]
        assert kept["failure"]["reason"] == "rejected" and kept["failure"]["code"] == "token_endpoint_unreachable"


@pytest.mark.parametrize(
    ("failure", "unknown"),
    [
        (httpx2.ConnectError("refused"), False),
        (httpx2.ConnectTimeout("unreachable"), False),
        (httpx2.PoolTimeout("no connection"), False),
        (httpx2.ReadTimeout("no answer"), True),
    ],
)
async def test_only_a_token_request_that_may_have_been_sent_has_an_unknown_outcome(
    failure: httpx2.TransportError, unknown: bool
) -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise failure

    client = OAuthClient(
        issuer="https://auth.test",
        token_endpoint="https://auth.test/token",
        revocation_endpoint=None,
        resource="https://mcp.test/mcp",
        client_id="client-1",
        authentication="none",
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(fail)) as http:
        with pytest.raises(OAuthError) as refused:
            await renew_access(http, client, "refresh-1")
    assert (refused.value.code, refused.value.unknown) == ("token_endpoint_unreachable", unknown)


async def test_token_renewal_keeps_the_etag_so_edits_and_tests_stay_current(service) -> None:  # type: ignore[no-untyped-def]
    async with remote(oauth=True) as server:
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        connection = await authorized(service, server, created)
        await expire_soon(service, created["id"])
        tested = await post(service, f"/connections/{created['id']}/test")
        assert tested["status"] == "succeeded" and server.issued == 2
        current = await view(service, created["id"])
        assert current["version"] == connection["version"] == tested["connection_version"]
        assert current["last_test"]["connection_version"] == current["version"]
        # The ETag read before the renewal still guards an edit.
        renamed = await patch(service, connection, {"name": "Renamed"})
        assert renamed["version"] == connection["version"] + 1 and renamed["credential_configured"]


async def test_browser_flows_need_a_login_session_and_are_bound_to_its_browser(service) -> None:  # type: ignore[no-untyped-def]
    async with (
        remote(oauth=True) as server,
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=service.app), base_url="https://service.test") as other,
    ):
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        item = f"{service.api}/connections/{created['id']}"
        response = await service.client.post(
            item + "/authorize", json={"return_url": RETURN_URL}, headers={"If-Match": etag(created)}
        )
        assert response.status_code == 200, response.text
        (cookie,) = [
            cookie
            for cookie in response.headers.get_list("set-cookie")
            if cookie.startswith(f"a13n_flow_{created['id']}=")
        ]
        assert all(part in cookie for part in ("HttpOnly", f"Path={CALLBACK_PATH}", "SameSite=lax"))

        # Another browser holding the link cannot complete the flow; it is refused and dropped.
        state, code = server.grant(response.json()["redirect_url"])
        callback = {"state": state, "code": code, "iss": server.url}
        refused = await other.get(CALLBACK_PATH, params=callback)
        assert refused.status_code == 303
        assert parse_qs(urlsplit(refused.headers["location"]).query)["error"] == ["browser_mismatch"]
        dropped = await view(service, created["id"])
        assert dropped["status"] == "pending" and not dropped["authorization_pending"]
        assert dropped["failure"]["code"] == "browser_mismatch" and server.issued == 0
        assert (await service.client.get(CALLBACK_PATH, params=callback)).status_code == 400

        # The initiating browser completes its own flow.
        current = await authorized(service, server, dropped)
        assert current["status"] == "ready"

        # An API key never starts a browser flow: it could hand the authorization URL to someone else.
        response = await other.post(
            item + "/authorize",
            json={"return_url": RETURN_URL},
            headers={"If-Match": etag(current), **await api_key(service)},
        )
        assert response.status_code == 403, response.text
        assert response.json()["error"]["message"] == "This operation requires a login session"
        assert not any("a13n_flow_" in cookie for cookie in response.headers.get_list("set-cookie"))
        assert not (await view(service, created["id"]))["authorization_pending"]


async def test_a_callback_during_a_refresh_supersedes_the_refresh(service) -> None:  # type: ignore[no-untyped-def]
    server = HeldRefresh()
    async with serve(server) as url:
        server.url = url
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        connection = await authorized(service, server, created)
        item = f"/connections/{created['id']}"
        redirect = await post(
            service, item + "/authorize", {"return_url": RETURN_URL}, **{"If-Match": etag(connection)}
        )
        await expire_soon(service, created["id"])
        refreshing = asyncio.create_task(post(service, item + "/test"))
        await server.arrived.wait()

        state, code = server.grant(redirect["redirect_url"])
        response = await service.client.get(CALLBACK_PATH, params={"state": state, "code": code, "iss": server.url})
        assert response.status_code == 303, response.text
        assert parse_qs(urlsplit(response.headers["location"]).query)["status"] == ["ready"]
        server.release.set()
        assert (await refreshing)["status"] == "failed"

        completed = await view(service, created["id"])
        assert completed["status"] == "ready" and completed["failure"] is None
        assert (await post(service, item + "/test"))["status"] == "succeeded"
        assert server.requests[-1]["authorization"] == "Bearer access-2" and server.presented == ["refresh-1"]


async def test_browser_flows_return_to_the_service_origin_and_expire(service) -> None:  # type: ignore[no-untyped-def]
    async with remote(oauth=True) as server:
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        item = f"{service.api}/connections/{created['id']}/authorize"
        console = "http://127.0.0.1:8000/connections/callback"
        for refused in ("http://127.0.0.1:8000.evil.test/callback", "http://127.0.0.1:8001/", "http://127.0.0.1:8000"):
            response = await service.client.post(
                item, json={"return_url": refused}, headers={"If-Match": etag(created)}
            )
            assert response.status_code == 400, refused
        response = await service.client.post(item, json={"return_url": console}, headers={"If-Match": etag(created)})
        assert response.status_code == 200, response.text
        assert (await view(service, created["id"]))["authorization_pending"]

        async with transaction(service.runtime.storage) as session:
            locked = await session.get(ConnectionRow, created["id"], with_for_update=True)
            assert locked is not None
            locked.authorization_expires_at = await now(session) - timedelta(seconds=1)
        assert not (await view(service, created["id"]))["authorization_pending"]
        state, code = server.grant(response.json()["redirect_url"])
        expired = await service.client.get(CALLBACK_PATH, params={"state": state, "code": code, "iss": server.url})
        assert expired.status_code == 303 and expired.headers["location"].startswith(console + "?")
        assert parse_qs(urlsplit(expired.headers["location"]).query)["error"] == ["authorization_expired"]


async def test_revocation_reports_what_the_remote_side_was_asked(service) -> None:  # type: ignore[no-untyped-def]
    async with remote(oauth=True) as server:
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        first = await authorized(service, server, created)
        # A new grant to the same client of the same issuer leaves the one it replaced alone: a server that keeps
        # one grant per user and client would end the new one with it.
        second = await authorized(service, server, first)
        assert server.revoked == [] and second["status"] == "ready"
        assert (await post(service, f"/connections/{created['id']}/test"))["status"] == "succeeded"

        server.revocation_status = 503
        failed = await post(service, f"/connections/{created['id']}/revoke", **{"If-Match": etag(second)})
        assert failed["remote_revocation"] == "failed" and not failed["credential_configured"]
        assert failed["failure"]["operation_kind"] == "revoke" and failed["failure"]["reason"] == "outcome_unknown"

        # Revoking is offboarding: a disabled connection's credential is still ended remotely.
        server.revocation_status = 200
        third = await authorized(service, server, failed)
        disabled = await patch(service, third, {"enabled": False})
        revoked = await post(service, f"/connections/{created['id']}/revoke", **{"If-Match": etag(disabled)})
        assert revoked["remote_revocation"] == "revoked" and not revoked["credential_configured"]
        assert server.revoked == ["refresh-2", "refresh-3"]

    bearer = await create(service, {"config": {"url": "http://127.0.0.1:9/mcp"}, "auth": "bearer"})
    revoked = await post(service, f"/connections/{bearer['id']}/revoke", **{"If-Match": etag(bearer)})
    assert revoked["remote_revocation"] == "skipped"


async def test_a_new_authorization_revokes_a_grant_made_to_another_client(service) -> None:  # type: ignore[no-untyped-def]
    async with remote(oauth=True, distinct_clients=True) as server:
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        first = await authorized(service, server, created)
        second = await authorized(service, server, first)
        assert server.revoked == ["refresh-1"] and second["status"] == "ready"


async def test_revocation_ends_credentials_of_an_archived_workspace(service) -> None:  # type: ignore[no-untyped-def]
    """Connections have no delete, so revoking is how offboarding ends the grants an archived workspace holds."""
    async with remote(oauth=True) as server:
        created = await create(service, {"config": {"url": f"{server.url}/mcp"}, "auth": "oauth"})
        connection = await authorized(service, server, created)
        workspace = (await service.client.get(service.workspace)).json()
        archived = await service.client.post(f"{service.workspace}/archive", headers={"If-Match": etag(workspace)})
        assert archived.status_code == 200, archived.text

        item = f"{service.api}/connections/{created['id']}"
        renamed = await service.client.patch(item, json={"name": "Renamed"}, headers={"If-Match": etag(connection)})
        assert renamed.status_code == 422 and renamed.json()["error"]["details"]["kind"] == "workspace"
        revoked = await post(service, f"/connections/{created['id']}/revoke", **{"If-Match": etag(connection)})
        assert revoked["remote_revocation"] == "revoked" and not revoked["credential_configured"]
        assert server.revoked == ["refresh-1"]


VERSION = "20260903_01"
COMPOSIO_KEY = "composio-api-key"


class Composio:
    """The Composio v3.1 endpoints hosted setup, the app catalogue and account tools use.

    Each hosted setup links a new connected account: `ca_1`, `ca_2` and so on.
    """

    def __init__(self) -> None:
        self.url = ""
        self.user_id = ""
        self.callback_url = ""
        self.accounts: list[str] = []
        self.executed: list[dict] = []
        self.revoked: list[str] = []
        self.listed = 0
        self.scheme = "OAUTH2"
        self.account_auth: dict = {"id": "ac_github", "auth_scheme": self.scheme}
        self.auth_config_reads = 0
        self.has_auth_config = True
        self.completions = 0
        self.inspections = 0
        app = FastAPI()
        app.middleware("http")(self._authenticate)
        app.get("/api/v3.1/toolkits")(self._toolkits)
        app.get("/api/v3.1/toolkits/github")(self._toolkit)
        app.get("/api/v3.1/auth_configs")(self._auth_configs)
        app.post("/api/v3.1/auth_configs")(self._create_auth_config)
        app.get("/api/v3.1/tools")(self._tools)
        app.post("/api/v3.1/connected_accounts/link")(self._link)
        app.post("/api/v3.1/connected_accounts/complete_auth")(self._complete)
        app.get("/api/v3.1/connected_accounts/{account}")(self._account)
        app.post("/api/v3.1/connected_accounts/{account}/revoke")(self._revoke)
        app.post("/api/v3.1/tools/execute/GITHUB_CREATE")(self._execute)
        self.app = app

    async def _authenticate(self, request: Request, call_next: Any) -> Any:
        if request.headers.get("x-api-key") != COMPOSIO_KEY:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    def _toolkits(self) -> dict:
        self.listed += 1
        slack = {**self._toolkit(), "slug": "slack", "name": "Slack"}
        return {"items": [self._toolkit(), slack]}

    def _toolkit(self) -> dict:
        return {
            "slug": "github",
            "name": "GitHub",
            "meta": {"version": VERSION, "description": "Code hosting"},
            "auth_schemes": [self.scheme],
            "composio_managed_auth_schemes": [self.scheme],
        }

    def _auth_configs(self) -> dict:
        self.auth_config_reads += 1
        config = {"id": "ac_github", "toolkit": {"slug": "github"}, "status": "ENABLED", "auth_scheme": self.scheme}
        return {"items": [{**config, "is_composio_managed": True}] if self.has_auth_config else []}

    async def _create_auth_config(self, request: Request) -> dict:
        body = await request.json()
        assert body["auth_config"]["type"] == "use_composio_managed_auth"
        self.has_auth_config = True
        return {"toolkit": {"slug": "github"}, "auth_config": self._auth_configs()["items"][0]}

    def _tools(self) -> dict:
        tool = {
            "slug": "GITHUB_CREATE",
            "toolkit": {"slug": "github"},
            "version": VERSION,
            "description": "Create an issue",
            "input_parameters": {"type": "object", "properties": {"title": {"type": "string"}}},
            "output_parameters": None,
        }
        return {"items": [tool], "toolkit_version": VERSION}

    async def _link(self, request: Request) -> dict:
        body = await request.json()
        assert body["auth_config_id"] == "ac_github"
        self.user_id, self.callback_url = body["user_id"], body["callback_url"]
        self.accounts.append(f"ca_{len(self.accounts) + 1}")
        expires = "2099-01-01T00:00:00Z"
        return {"connected_account_id": self.accounts[-1], "expires_at": expires, "redirect_url": f"{self.url}/link/1"}

    async def _complete(self, request: Request) -> dict:
        self.completions += 1
        body = await request.json()
        assert body == {"session_uri": "verifier-session", "user_id": self.user_id}
        return {"connected_account_id": self.accounts[-1], "toolkit_slug": "github"}

    def _account(self, account: str) -> dict:
        self.inspections += 1
        return {
            "id": account,
            "toolkit": {"slug": "github"},
            "user_id": self.user_id,
            "status": "ACTIVE",
            "auth_config": self.account_auth,
        }

    def _revoke(self, account: str) -> dict:
        self.revoked.append(account)
        return {"status": "REVOKED"}

    async def _execute(self, request: Request) -> dict:
        self.executed.append(await request.json())
        return {"successful": True, "data": {"number": 7}}


@asynccontextmanager
async def composio_connection(  # type: ignore[no-untyped-def]
    service, monkeypatch: pytest.MonkeyPatch, *, scheme: str = "OAUTH2", selector: str = "ac_github"
) -> AsyncIterator[tuple[Composio, dict]]:
    from a13n_harness.providers.connector.composio import catalog, runtime

    composio = Composio()
    composio.scheme = scheme
    composio.has_auth_config = not selector.startswith("create:")
    composio.account_auth = {"id": "ac_github", "auth_scheme": scheme}
    async with serve(composio.app) as url:
        composio.url = url
        monkeypatch.setattr(runtime, "COMPOSIO_ENDPOINT", url)
        monkeypatch.setattr(catalog, "COMPOSIO_ENDPOINT", url)
        monkeypatch.setattr(runtime, "COMPOSIO_CONNECT_ENDPOINT", url)
        response = await service.client.post(
            service.api + "/connector-providers",
            json={"type": "composio", "name": "Composio", "credential": {"api_key": COMPOSIO_KEY}},
        )
        assert response.status_code == 201, response.text
        created = await post(
            service,
            "/connections",
            {
                "type": "composio",
                "name": "GitHub",
                "connector_provider_id": response.json()["id"],
                "auth": "account",
                "config": {
                    "app": "github",
                    "actions": ["GITHUB_CREATE"],
                    "setup": {"auth_config_id": selector, "toolkit_version": VERSION},
                },
            },
            status=201,
        )
        yield composio, created


async def complete_account(service, composio: Composio) -> dict:  # type: ignore[no-untyped-def]
    state = parse_qs(urlsplit(composio.callback_url).query)["state"][0]
    callback = await service.client.get(CALLBACK_PATH, params={"state": state, "session_uri": "verifier-session"})
    assert callback.status_code == 200, callback.text
    return callback.json()


# Each selector completes a valid account and refuses a changed one; each way of changing the account's
# authentication configuration is refused once. The comparison does not depend on the selector.
@pytest.mark.parametrize(
    "scheme,selector,change",
    [
        ("OAUTH2", "ac_github", "valid"),
        ("OAUTH2", "create:OAUTH2", "valid"),
        ("API_KEY", "ac_github", "valid"),
        ("OAUTH2", "ac_github", "wrong_id"),
        ("OAUTH2", "create:OAUTH2", "wrong_scheme"),
        ("API_KEY", "ac_github", "missing_id"),
        ("OAUTH2", "ac_github", "missing_scheme"),
        ("OAUTH2", "create:OAUTH2", "null"),
        ("API_KEY", "ac_github", "empty"),
    ],
)
async def test_account_completion_checks_setup_identity(  # type: ignore[no-untyped-def]
    service, monkeypatch: pytest.MonkeyPatch, scheme: str, selector: str, change: str
) -> None:
    async with composio_connection(service, monkeypatch, scheme=scheme, selector=selector) as (composio, created):
        item = f"/connections/{created['id']}"
        await post(service, item + "/authorize", {}, **{"If-Match": etag(created)})
        stored = await row(service, created["id"])
        flow = reveal(
            service.runtime.keys, stored.organization_id, stored.id, "authorization", stored.authorization, AccountFlow
        )
        assert flow.expected_metadata == {"auth_config_id": "ac_github", "auth_scheme": scheme}
        reads = composio.auth_config_reads
        if change == "wrong_id":
            composio.account_auth["id"] = "ac_other"
        elif change == "wrong_scheme":
            composio.account_auth["auth_scheme"] = "BASIC"
        elif change == "missing_id":
            del composio.account_auth["id"]
        elif change == "missing_scheme":
            del composio.account_auth["auth_scheme"]
        elif change == "null":
            composio.account_auth = {"id": None, "auth_scheme": None}
        elif change == "empty":
            composio.account_auth = {}
        # The callback must not resolve a mutable selector again.
        composio.scheme = "BASIC"
        result = await complete_account(service, composio)
        valid = change == "valid"
        assert result["error"] == (None if valid else "account_metadata_mismatch")
        assert composio.auth_config_reads == reads
        assert composio.completions == (1 if scheme == "OAUTH2" else 0)
        assert composio.inspections == 1
        stored = await row(service, created["id"])
        assert (stored.credential is not None) is valid
        assert stored.status == ("ready" if valid else "pending")
        assert stored.authorization is None and stored.operation_id is None
        if not valid:
            assert stored.failure["reason"] == "rejected"


@pytest.mark.parametrize(
    "expected,actual,valid",
    [
        ({}, {}, True),
        ({"realm": "chosen"}, {"realm": "chosen"}, True),
        ({"realm": "chosen"}, {"realm": "other"}, False),
    ],
)
async def test_account_metadata_predicates_are_provider_neutral(  # type: ignore[no-untyped-def]
    service, monkeypatch: pytest.MonkeyPatch, expected: dict, actual: dict, valid: bool
) -> None:
    from a13n_harness.providers.connector.composio.runtime import ComposioProvider

    start_setup = ComposioProvider.start_setup
    inspect_setup = ComposioProvider.inspect_setup

    async def start(self, **kwargs):  # type: ignore[no-untyped-def]
        started = await start_setup(self, **kwargs)
        return started.model_copy(update={"expected_metadata": expected})

    async def inspect(self, **kwargs):  # type: ignore[no-untyped-def]
        inspection = await inspect_setup(self, **kwargs)
        return inspection.model_copy(update={"safe_metadata": {**actual, "display_name": "Account"}})

    monkeypatch.setattr(ComposioProvider, "start_setup", start)
    monkeypatch.setattr(ComposioProvider, "inspect_setup", inspect)
    async with composio_connection(service, monkeypatch) as (composio, created):
        await post(service, f"/connections/{created['id']}/authorize", {}, **{"If-Match": etag(created)})
        result = await complete_account(service, composio)
        assert result["error"] == (None if valid else "account_metadata_mismatch")
        assert ((await row(service, created["id"])).credential is not None) is valid


async def test_composio_account_is_set_up_through_hosted_flow_and_runs_its_pinned_actions(  # type: ignore[no-untyped-def]
    service, monkeypatch: pytest.MonkeyPatch
) -> None:
    from a13n_harness.providers.connector.composio import catalog, runtime

    composio = Composio()
    async with serve(composio.app) as url:
        composio.url = url
        for module, name in ((runtime, "COMPOSIO_ENDPOINT"), (catalog, "COMPOSIO_ENDPOINT")):
            monkeypatch.setattr(module, name, url)
        monkeypatch.setattr(runtime, "COMPOSIO_CONNECT_ENDPOINT", url)
        response = await service.client.post(
            service.api + "/connector-providers",
            json={"type": "composio", "name": "Composio", "credential": {"api_key": COMPOSIO_KEY}},
        )
        assert response.status_code == 201, response.text
        provider_id = response.json()["id"]
        provider = f"/connector-providers/{provider_id}"
        apps = (await service.client.get(service.api + provider + "/apps", params={"query": "git"})).json()
        assert [app["key"] for app in apps["items"]] == ["github"] and apps["next_cursor"] is None
        assert apps["items"][0]["authentication_methods"] == ["OAUTH2"]
        # The catalogue is cached briefly; `refresh` reads it anew. A cursor stays bound to its query.
        first = (await service.client.get(service.api + provider + "/apps", params={"limit": 1})).json()
        assert [app["key"] for app in first["items"]] == ["github"] and composio.listed == 1
        rest = {"limit": 1, "cursor": first["next_cursor"]}
        assert [
            app["key"]
            for app in (await service.client.get(service.api + provider + "/apps", params=rest)).json()["items"]
        ] == ["slack"]
        response = await service.client.get(service.api + provider + "/apps", params={**rest, "query": "s"})
        assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_cursor"
        await service.client.get(service.api + provider + "/apps", params={"refresh": True})
        assert composio.listed == 2
        app = (await service.client.get(service.api + provider + "/apps/github")).json()
        assert app["setup_schema"]["properties"]["toolkit_version"]["const"] == VERSION
        actions = (await service.client.get(service.api + provider + "/apps/github/actions")).json()
        assert [action["name"] for action in actions["items"]] == ["GITHUB_CREATE"]

        setup = {"auth_config_id": "ac_github", "toolkit_version": VERSION}
        created = await post(
            service,
            "/connections",
            {
                "type": "composio",
                "name": "GitHub",
                "connector_provider_id": provider_id,
                "auth": "account",
                "config": {"app": "github", "actions": ["GITHUB_CREATE"], "setup": setup},
            },
            status=201,
        )
        assert created["status"] == "pending"
        item = f"/connections/{created['id']}"
        # Before an account is bound, the tools are the app's catalogue, and a test fails.
        listed = (await service.client.get(service.api + item + "/tools")).json()
        assert [tool["name"] for tool in listed["items"]] == ["GITHUB_CREATE"]
        unbound = await post(service, item + "/test")
        assert unbound["status"] == "failed" and "not authorized" in unbound["message"]

        async def link(connection: dict) -> dict:
            redirect = await post(service, item + "/authorize", {}, **{"If-Match": etag(connection)})
            assert redirect["redirect_url"] == f"{url}/link/1" and composio.user_id == created["id"]
            state = parse_qs(urlsplit(composio.callback_url).query)["state"][0]
            callback = await service.client.get(
                CALLBACK_PATH, params={"state": state, "session_uri": "verifier-session"}
            )
            assert callback.status_code == 200, callback.text
            assert callback.json() == {"connection_id": created["id"], "status": "ready", "error": None}
            return await view(service, created["id"])

        # The hosted setup is a browser flow: an API key never starts one.
        refused = await service.client.post(
            service.api + item + "/authorize",
            json={},
            headers={"If-Match": etag(created), **await api_key(service)},
        )
        assert refused.status_code == 403 and composio.accounts == []
        connection = await link(created)
        assert connection["credential_configured"] and "ca_1" not in json.dumps(connection)
        # Linking again replaces the account, and the replaced one is revoked at the provider.
        connection = await link(connection)
        assert composio.revoked == ["ca_1"]

        dispatches: list[ToolDispatch] = []
        async with opened(service, [ConnectionSelection(connection_id=created["id"])], {}, dispatches) as capabilities:
            seen = await run_tool(capabilities, "GITHUB_CREATE", {"title": "Bug"})
        assert seen["tools"] == ["GITHUB_CREATE"] and "7" in seen["results"][0]
        assert dispatches == [ToolDispatch(created["id"], provider_id, "GITHUB_CREATE", "call-1")]
        executed = composio.executed[0]
        assert executed["arguments"] == {"title": "Bug"} and executed["connected_account_id"] == "ca_2"
        assert executed["version"] == VERSION
        # Connector actions are always loaded; only an MCP server's listing is searched.
        deferred = ConnectionSelection(connection_id=created["id"], defer_loading=True)
        async with short_session(service.runtime.storage) as session:
            with pytest.raises(ServiceError, match="applies only to MCP connections"):
                await validate_selection(session, admin(service), scope(service), deferred)

        revoked = await post(service, item + "/revoke", **{"If-Match": etag(connection)})
        assert revoked["status"] == "pending" and revoked["remote_revocation"] == "revoked"
        assert composio.revoked == ["ca_1", "ca_2"]


async def test_mcp_native_runs_have_independent_authenticated_client_lifetimes(service) -> None:  # type: ignore[no-untyped-def]
    async with remote() as server:
        created = await create(
            service,
            {
                "config": {"url": f"{server.url}/mcp", "headers": ["x-api-key"], "tools": ["echo"]},
                "auth": "headers",
                "credential": {"headers": {"x-api-key": SECRET}},
            },
        )
        dispatches: list[ToolDispatch] = []
        selection = ConnectionSelection(connection_id=created["id"])
        async with opened(
            service, [selection], {created["id"]: {"x-trace": "shared-attempt"}}, dispatches
        ) as capabilities:
            # Native attempts may overlap, and later attempts must not inherit a closed toolset/client.
            results = await asyncio.gather(
                *(run_tool(capabilities, "echo", {"text": value}) for value in ("one", "two"))
            )
            assert sorted(result["results"][0] for result in results) == ["one", "two"]
            assert (await run_tool(capabilities, "echo", {"text": "three"}))["results"] == ["three"]
        assert sorted(server.echoed) == ["one", "three", "two"]
        assert len(dispatches) == 3
        assert all(
            request.get("x-api-key") == SECRET and request.get("x-trace") == "shared-attempt"
            for request in server.requests
        )
        # Another attempt has its own caller headers, even for the same connection.
        async with opened(
            service, [selection], {created["id"]: {"x-trace": "next-attempt"}}, dispatches
        ) as capabilities:
            assert (await run_tool(capabilities, "echo", {"text": "four"}))["results"] == ["four"]
        assert server.requests[-1]["x-trace"] == "next-attempt"


def test_overlong_header_value_reports_its_size_without_echoing_the_value() -> None:
    from a13n_service.resources.connections.headers import normalize_headers

    with pytest.raises(ValueError, match="at most 4096 ASCII bytes") as error:
        normalize_headers({"x-api-key": "s" * 4097})
    assert "s" * 100 not in str(error.value)
