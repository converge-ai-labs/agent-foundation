"""Stateless Streamable HTTP MCP Gateway for logical Connector servers."""

from __future__ import annotations

import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from a13n_harness.tools import HARNESS_TOOL_METADATA_KEY
from mcp import types
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.shared.exceptions import McpError
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from a13n_service.iam import (
    AuthenticationError,
    AuthorizationError,
    RequestAuthenticator,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.storage import short_session

from .capability import ConnectorAttemptAuthorizer, ConnectorCapabilityCodec
from .domain import PrincipalRef
from .errors import ConnectorError
from .provider import ConnectorProviderContext, ConnectorProviderTool
from .runtime import ConnectorProviderRuntime

_invocation_context: ContextVar[ConnectorMCPInvocation] = ContextVar("connector_mcp_invocation")
_CONNECTOR_PATH_PREFIX = "con_"


@dataclass(frozen=True, slots=True)
class ConnectorMCPInvocation:
    organization_id: str
    workspace_id: str
    connector_id: str
    connector_revision_id: str
    connection_id: str | None
    effective_tools: tuple[str, ...] | None
    provider_contract_version: str
    principal: PrincipalRef
    request_id: str


class ConnectorMCPRequestAuthenticator(Protocol):
    async def __call__(
        self,
        request: Request,
        *,
        connector_id: str,
        connection_id: str | None,
    ) -> ConnectorMCPInvocation: ...


class StandardConnectorMCPAuthenticator:
    """Authenticate API Keys and authorize current Workspace Connector use."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        request_authenticator: RequestAuthenticator,
        runtime: ConnectorProviderRuntime,
    ) -> None:
        self._sessions = sessions
        self._request_authenticator = request_authenticator
        self._runtime = runtime

    async def __call__(
        self,
        request: Request,
        *,
        connector_id: str,
        connection_id: str | None,
    ) -> ConnectorMCPInvocation:
        try:
            actor = await self._request_authenticator(request)
        except AuthenticationError:
            raise
        except Exception as error:
            raise AuthenticationError("authentication failed") from error
        if actor.auth_method != "api_key":
            raise AuthenticationError("standard Connector MCP requires an API Key")
        try:
            async with short_session(self._sessions) as session:
                workspace = await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=actor.boundary_workspace_id,
                    action=WorkspaceAction.connector_invoke,
                )
        except AuthorizationError as error:
            raise ConnectorError("Connector invocation is forbidden.", code="connector_invoke_forbidden") from error

        revision_id = await self._runtime.latest_revision_id(
            organization_id=workspace.organization_id,
            workspace_id=workspace.workspace_id,
            connector_id=connector_id,
        )
        selected_connection_id = await self._runtime.resolve_standard_connection(
            organization_id=workspace.organization_id,
            workspace_id=workspace.workspace_id,
            connector_revision_id=revision_id,
            requested_connection_id=connection_id,
            principal=actor.principal,
        )
        return ConnectorMCPInvocation(
            organization_id=workspace.organization_id,
            workspace_id=workspace.workspace_id,
            connector_id=connector_id,
            connector_revision_id=revision_id,
            connection_id=selected_connection_id,
            effective_tools=None,
            provider_contract_version=await self._runtime.provider_contract_version(revision_id),
            principal=actor.principal,
            request_id=_request_id(request),
        )


class AttemptConnectorMCPAuthenticator:
    """Verify one signed capability and its current durable Attempt fence."""

    def __init__(
        self,
        codec: ConnectorCapabilityCodec,
        attempt_authorizer: ConnectorAttemptAuthorizer,
    ) -> None:
        self._codec = codec
        self._attempt_authorizer = attempt_authorizer

    async def __call__(
        self,
        request: Request,
        *,
        connector_id: str,
        connection_id: str | None,
    ) -> ConnectorMCPInvocation:
        if connection_id is not None:
            raise ConnectorError("Internal Connection selection is fixed by the capability.", code="invalid_request")
        claims = self._codec.verify(_bearer_token(request))
        if claims.connector_id != connector_id:
            raise ConnectorError("Connector capability target is invalid.", code="connector_capability_invalid")
        principal = await self._attempt_authorizer.authorize_connector_attempt(claims)
        return ConnectorMCPInvocation(
            organization_id=claims.organization_id,
            workspace_id=claims.workspace_id,
            connector_id=claims.connector_id,
            connector_revision_id=claims.connector_revision_id,
            connection_id=claims.connection_id,
            effective_tools=claims.effective_tools,
            provider_contract_version=claims.provider_contract_version,
            principal=principal,
            request_id=_request_id(request),
        )


class ConnectorMCPGateway:
    """One MCP server implementation shared by standard and internal routes."""

    def __init__(
        self,
        runtime: ConnectorProviderRuntime,
        *,
        standard_authenticator: ConnectorMCPRequestAuthenticator | None,
        attempt_authenticator: ConnectorMCPRequestAuthenticator | None,
        operation_timeout_seconds: float = 30,
    ) -> None:
        if operation_timeout_seconds <= 0 or operation_timeout_seconds > 300:
            raise ValueError("Connector MCP operation timeout is invalid")
        self._runtime = runtime
        self._operation_timeout_seconds = operation_timeout_seconds
        self._server: Server[Any] = Server("foundation-connector-gateway")
        self._manager = StreamableHTTPSessionManager(
            self._server,
            json_response=True,
            stateless=True,
            max_request_body_size=4 * 1024 * 1024,
        )
        self.standard_app: ASGIApp = _ConnectorMCPASGI(self._manager, standard_authenticator)
        self.internal_app: ASGIApp = _ConnectorMCPASGI(self._manager, attempt_authenticator)
        self._register_handlers()

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        async with self._manager.run():
            yield

    def _register_handlers(self) -> None:
        @self._server.list_tools()
        async def list_tools() -> list[types.Tool]:
            invocation = _current_invocation()
            try:
                tools = await self._runtime.list_selected_tools(
                    organization_id=invocation.organization_id,
                    workspace_id=invocation.workspace_id,
                    connector_id=invocation.connector_id,
                    connector_revision_id=invocation.connector_revision_id,
                    connection_id=invocation.connection_id,
                    effective_tools=invocation.effective_tools,
                    provider_contract_version=invocation.provider_contract_version,
                    principal=invocation.principal,
                    context=self._provider_context(invocation.request_id, "list"),
                )
            except ConnectorError as error:
                raise _mcp_error(error) from None
            except Exception:
                raise _mcp_internal_error() from None
            return [_mcp_tool(tool) for tool in tools]

        @self._server.call_tool(validate_input=False)
        async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
            invocation = _current_invocation()
            try:
                result = await self._runtime.call_tool(
                    organization_id=invocation.organization_id,
                    workspace_id=invocation.workspace_id,
                    connector_id=invocation.connector_id,
                    connector_revision_id=invocation.connector_revision_id,
                    connection_id=invocation.connection_id,
                    effective_tools=invocation.effective_tools,
                    provider_contract_version=invocation.provider_contract_version,
                    provider_tool_name=name,
                    arguments=arguments,
                    principal=invocation.principal,
                    context=self._provider_context(invocation.request_id, f"call:{name}"),
                )
                return _tool_result(result.value)
            except ConnectorError as error:
                return _tool_error(error)
            except Exception:
                return _tool_error(ConnectorError("Connector operation failed.", code="internal_error"))

    def _provider_context(self, request_id: str, operation: str) -> ConnectorProviderContext:
        operation_id = f"{request_id}:{operation}:{secrets.token_hex(8)}"
        return ConnectorProviderContext(
            operation_id=operation_id[:200],
            deadline=datetime.now(UTC) + timedelta(seconds=self._operation_timeout_seconds),
        )


class _ConnectorMCPASGI:
    def __init__(
        self,
        manager: StreamableHTTPSessionManager,
        authenticator: ConnectorMCPRequestAuthenticator | None,
    ) -> None:
        self._manager = manager
        self._authenticator = authenticator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await _http_error(send, 404, "not_found")
            return
        path = scope.get("path", "")
        root_path = scope.get("root_path", "")
        if root_path and path.startswith(root_path):
            path = path[len(root_path) :]
        connector_id = _connector_id(path)
        if connector_id is None:
            await _http_error(send, 404, "not_found")
            return
        if scope.get("method") != "POST":
            await _http_error(send, 405, "method_not_allowed")
            return
        if self._authenticator is None:
            await _http_error(send, 503, "connector_service_unavailable")
            return
        request = Request(scope, receive=receive)
        requested_connection = request.headers.get("X-Foundation-Connection-Id")
        try:
            invocation = await self._authenticator(
                request,
                connector_id=connector_id,
                connection_id=requested_connection,
            )
        except AuthenticationError:
            await _http_error(send, 401, "authentication_required")
            return
        except ConnectorError as error:
            await _http_error(send, _http_status(error.code), error.code)
            return
        token = _invocation_context.set(invocation)
        try:
            await self._manager.handle_request(scope, receive, send)
        finally:
            _invocation_context.reset(token)


def _mcp_tool(tool: ConnectorProviderTool) -> types.Tool:
    effects = set(tool.effects)
    return types.Tool(
        name=tool.name,
        description=tool.description,
        inputSchema=dict(tool.parameters_json_schema),
        annotations=types.ToolAnnotations(
            readOnlyHint=bool(effects) and effects <= {"read"},
            destructiveHint=bool(effects & {"delete"}),
            idempotentHint=tool.idempotency in {"read_only", "provider_key"},
            openWorldHint=bool(effects & {"external_communication"}),
        ),
        _meta={
            HARNESS_TOOL_METADATA_KEY: {
                "tool_id": tool.tool_id,
                "effects": list(tool.effects),
                "credential_audiences": list(tool.credential_audiences),
                "idempotency": tool.idempotency,
                "output_policy": dict(tool.output_policy),
            },
        },
    )


def _tool_result(value: JsonValue) -> types.CallToolResult:
    encoded = json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    structured = value if isinstance(value, dict) else None
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=encoded)],
        structuredContent=structured,
        isError=False,
    )


def _tool_error(error: ConnectorError) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=f"Connector operation failed: {error.code}")],
        isError=True,
    )


def _mcp_error(error: ConnectorError) -> McpError:
    return McpError(types.ErrorData(code=-32000, message="Connector operation failed.", data={"code": error.code}))


def _mcp_internal_error() -> McpError:
    return McpError(types.ErrorData(code=-32603, message="Connector operation failed."))


def _current_invocation() -> ConnectorMCPInvocation:
    try:
        return _invocation_context.get()
    except LookupError:
        raise _mcp_internal_error() from None


def _connector_id(path: str) -> str | None:
    candidate = path.strip("/")
    if not candidate.startswith(_CONNECTOR_PATH_PREFIX) or "/" in candidate or len(candidate) > 72:
        return None
    return candidate


def _bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.casefold() != "bearer" or not token or " " in token:
        raise ConnectorError("Connector capability is missing.", code="connector_capability_invalid")
    return token


def _request_id(request: Request) -> str:
    supplied = request.headers.get("X-Request-ID")
    if supplied and len(supplied) <= 128 and all(0x21 <= ord(character) <= 0x7E for character in supplied):
        return supplied
    return f"mcp-{secrets.token_hex(12)}"


def _http_status(code: str) -> int:
    if code in {"authentication_required", "connector_capability_invalid"}:
        return 401
    if code in {"connector_invoke_forbidden", "connector_capability_fenced"}:
        return 403
    if code == "not_found":
        return 404
    if code in {"dependency_unavailable", "provider_unavailable", "connector_service_unavailable"}:
        return 503
    return 400


async def _http_error(send: Send, status_code: int, code: str) -> None:
    response = JSONResponse(status_code=status_code, content={"error": code})
    await response({"type": "http", "asgi": {"version": "3.0"}}, _empty_receive, send)


async def _empty_receive() -> dict[str, Any]:
    return {"type": "http.disconnect"}


__all__ = [
    "AttemptConnectorMCPAuthenticator",
    "ConnectorMCPGateway",
    "ConnectorMCPInvocation",
    "ConnectorMCPRequestAuthenticator",
    "StandardConnectorMCPAuthenticator",
]
