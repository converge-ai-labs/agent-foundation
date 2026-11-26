"""Expose a host-selected capability set through the current ACP turn."""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from inspect import isawaitable, signature
from typing import Any, cast

from acp import RequestError, schema
from aiohttp import web
from pydantic import ValidationError
from pydantic_ai import ModelRetry, RunContext, ToolDenied, ToolReturn
from pydantic_ai.agent import _build_run_capabilities
from pydantic_ai.capabilities import AbstractCapability, CombinedCapability
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred, UnexpectedModelBehavior
from pydantic_ai.messages import InstructionPart, ToolCallPart
from pydantic_ai.tool_manager import ToolManager
from pydantic_ai.toolsets import AbstractToolset, CombinedToolset
from pydantic_ai.toolsets.prepared import PreparedToolset
from pydantic_core import to_jsonable_python

from a13n_harness.context import AgentContext
from a13n_harness.model_context import (
    ModelContextInputOrigin,
    ModelContextProjectionRequest,
    ModelContextRequestKind,
    _project_model_context,
)

from .transport import AcpError

_MCP_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
_SERVER_NAME = "harness-host"
_SERVER_PREFIXES = (f"mcp__{_SERVER_NAME}__", f"mcp_{_SERVER_NAME}_", f"{_SERVER_NAME}/", f"{_SERVER_NAME}.")
_STDIO_URL = "A13N_ACP_MCP_URL"
_STDIO_TOKEN = "A13N_ACP_MCP_TOKEN"


def acp_host_tool_name(name: str) -> str:
    """Return the canonical name for a qualified harness-host tool."""
    for prefix in _SERVER_PREFIXES:
        if name.startswith(prefix) and len(name) > len(prefix):
            return name[len(prefix) :]
    return name


@dataclass(frozen=True)
class AcpHostToolScope:
    """Capability instances and context owned by one Run."""

    _bridge: AcpHostToolBridge
    context: RunContext[AgentContext]
    capability: AbstractCapability[AgentContext]
    toolset: AbstractToolset[AgentContext]

    async def instructions(self) -> tuple[str, ...]:
        sources = self.capability.get_instructions()
        if sources is None:
            return ()
        if isinstance(sources, str | InstructionPart) or callable(sources):
            sources = [sources]
        texts: list[str] = []
        for source in sources:
            if callable(source):
                value = (
                    cast(Callable[..., Any], source)(self.context)
                    if signature(source).parameters
                    else cast(Callable[[], Any], source)()
                )
            else:
                value = source
            value = await value if isawaitable(value) else value
            if value is not None:
                texts.append(value.content if isinstance(value, InstructionPart) else str(value))
        return tuple(filter(None, texts))

    async def prompt_context(self) -> tuple[str, ...]:
        projection = await _project_model_context(
            self.context,
            ModelContextProjectionRequest(
                kind=ModelContextRequestKind.INPUT, input_origin=ModelContextInputOrigin.USER
            ),
        )
        projection._confirm(projection.blocks)
        return tuple(block.content for block in projection.blocks)

    async def _manager(self) -> ToolManager[AgentContext]:
        return await ToolManager(self.toolset, root_capability=self.capability).for_run_step(self.context)

    async def _describe_tools(self) -> list[dict[str, Any]]:
        manager = await self._manager()
        return [
            {
                "name": tool.name,
                "inputSchema": tool.parameters_json_schema,
                **({"description": tool.description} if tool.description else {}),
            }
            for tool in manager.tool_defs
            if tool.kind == "function"
        ]

    async def _call(self, params: dict[str, Any], anchor: str | None) -> dict[str, Any]:
        manager = await self._manager()
        name, arguments = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise RequestError.invalid_params()
        if not any(tool.name == name and tool.kind == "function" for tool in manager.tool_defs):
            raise RequestError.invalid_params({"code": "acp_tool_unavailable"})
        self._bridge._tool_call_started = True
        try:
            # The fallback ID identifies the turn, not a fabricated tool card.
            result = await manager.handle_call(
                ToolCallPart(name, arguments, tool_call_id=anchor or self.context.deps.run_id)
            )
        except (ModelRetry, ValidationError) as exc:
            return _tool_result(str(exc), error=True)
        except (ApprovalRequired, CallDeferred):
            return _tool_result("This tool requires an inline interaction handler.", error=True)
        except UnexpectedModelBehavior:
            return _tool_result("The host could not complete the tool call.", error=True)
        if isinstance(result, ToolDenied):
            return _tool_result(result.message, error=True)
        return _tool_result(result.return_value if isinstance(result, ToolReturn) else result)


def _tool_result(value: Any, *, error: bool = False) -> dict[str, Any]:
    data = to_jsonable_python(value)
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": error}


class AcpHostToolBridge:
    """One dispatcher for HTTP, local stdio forwarding, and the pinned ACP profile."""

    def __init__(
        self,
        capabilities: Sequence[AbstractCapability[AgentContext]],
        *,
        toolsets: Sequence[AbstractToolset[AgentContext]] = (),
    ) -> None:
        self._capabilities = tuple(capabilities)
        self._toolsets = tuple(toolsets)
        self._scope: AcpHostToolScope | None = None
        self._turn_id: str | None = None
        self._tool_call_started = False
        self._calls: set[asyncio.Task[dict[str, Any]]] = set()
        self._active_names: dict[str, str | None] = {}
        self._tool_catalog: list[dict[str, Any]] = []
        self._token: str | None = None
        self._server_id: str | None = None
        self._connections: set[str] = set()

    @asynccontextmanager
    async def prepare(
        self,
        context: RunContext[AgentContext],
        *,
        capabilities: Sequence[AbstractCapability[AgentContext]] = (),
        toolsets: Sequence[AbstractToolset[AgentContext]] = (),
    ) -> AsyncIterator[AcpHostToolScope]:
        capability = await CombinedCapability((*self._capabilities, *capabilities)).for_run(context)
        # Capability-owned toolsets resolve ownership through the same registry
        # as a native agent step, including generated IDs for anonymous capabilities.
        context = replace(context, root_capability=capability, capabilities=_build_run_capabilities(capability))
        capability_tools = capability.get_toolset()
        assert capability_tools is None or isinstance(capability_tools, AbstractToolset)
        toolset: AbstractToolset[AgentContext] = CombinedToolset(
            [
                *self._toolsets,
                *toolsets,
                *([capability_tools] if capability_tools is not None else []),
            ]
        )
        toolset = PreparedToolset(toolset, capability.prepare_tools)
        toolset = capability.get_wrapper_toolset(toolset) or toolset
        toolset = await toolset.for_run(context)
        async with toolset:
            yield AcpHostToolScope(self, context, capability, toolset)

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[None]:
        if self._token is not None:
            raise AcpError("acp_mcp_busy", "The host tool bridge already belongs to an ACP connection.")
        self._token = secrets.token_urlsafe(32)
        try:
            yield
        finally:
            self._token = None
            self._server_id = None
            self._connections.clear()
            self._tool_catalog = []

    def _require_scope(self, scope: AcpHostToolScope) -> None:
        if scope._bridge is not self:
            raise AcpError("acp_mcp_scope_mismatch", "Prepare the Run scope with this connection's bridge.")

    async def _discover(self, scope: AcpHostToolScope) -> None:
        self._require_scope(scope)
        self._tool_catalog = await scope._describe_tools()

    def _bind(self, scope: AcpHostToolScope, turn_id: str) -> None:
        self._require_scope(scope)
        if self._turn_id is not None:
            raise AcpError("acp_mcp_busy", "The host tool bridge already has an active turn.")
        self._tool_call_started = False
        self._scope, self._turn_id = scope, turn_id

    def _revoke(self) -> None:
        self._scope = None
        for task in self._calls:
            if not task.cancelling():
                task.cancel()

    async def _unbind(self) -> None:
        self._revoke()
        await asyncio.gather(*self._calls, return_exceptions=True)
        self._active_names.clear()
        self._turn_id = None

    def _observe(self, update: schema.ToolCallStart | schema.ToolCallProgress) -> None:
        if update.status in {"completed", "failed"}:
            self._active_names.pop(update.tool_call_id, None)
        elif isinstance(update, schema.ToolCallStart) or (
            update.name is not None and update.tool_call_id in self._active_names
        ):
            self._active_names[update.tool_call_id] = update.name

    async def _dispatch(self, method: str, params: dict[str, Any], call_id: str | None = None) -> dict[str, Any]:
        if method == "initialize":
            version = params.get("protocolVersion")
            return {
                "protocolVersion": version if isinstance(version, str) and version in _MCP_VERSIONS else "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": _SERVER_NAME, "version": "1"},
            }
        if method in {"ping", "notifications/initialized"}:
            return {}
        if method == "tools/list":
            return {"tools": self._tool_catalog}
        if method != "tools/call":
            raise RequestError.method_not_found(method)
        scope = self._scope
        if scope is None:
            raise RequestError.invalid_request({"code": "acp_run_inactive"})
        name = params.get("name")
        if call_id is None and isinstance(name, str):
            matches = [key for key, active_name in self._active_names.items() if active_name == name]
            if not matches:
                names = {prefix + name for prefix in _SERVER_PREFIXES}
                matches = [key for key, active_name in self._active_names.items() if active_name in names]
            if len(matches) == 1:
                call_id = matches[0]
        task = asyncio.create_task(scope._call(params, call_id))
        self._calls.add(task)
        try:
            result = await task
            if self._scope is not scope:
                raise RequestError.invalid_request({"code": "acp_run_inactive"})
            return result
        except asyncio.CancelledError:
            if self._scope is not scope:
                raise RequestError.invalid_request({"code": "acp_run_inactive"}) from None
            raise
        finally:
            self._calls.discard(task)

    def _acp_server(self) -> schema.AcpMcpServer:
        self._server_id = secrets.token_urlsafe(24)
        return schema.AcpMcpServer(name=_SERVER_NAME, server_id=self._server_id)

    def _connect(self, server_id: str) -> schema.ConnectMcpResponse:
        if self._server_id is None or server_id != self._server_id:
            raise RequestError.resource_not_found()
        connection_id = secrets.token_urlsafe(24)
        self._connections.add(connection_id)
        return schema.ConnectMcpResponse(connection_id=connection_id)

    def _disconnect(self, connection_id: str) -> None:
        self._connections.discard(connection_id)

    async def _message(
        self, connection_id: str, method: str, params: dict[str, Any], metadata: dict[str, Any]
    ) -> dict[str, Any]:
        if connection_id not in self._connections:
            raise RequestError.resource_not_found()
        call_id = metadata.get("toolCallId")
        return await self._dispatch(method, params, call_id if isinstance(call_id, str) and call_id else None)

    @asynccontextmanager
    async def _loopback(self, *, http: bool) -> AsyncIterator[schema.HttpMcpServer | schema.McpServerStdio]:
        token = self._token
        assert token is not None
        app = web.Application(client_max_size=16 * 1024 * 1024)
        app.router.add_post("/mcp", self._http_request)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, "127.0.0.1", 0).start()
            url = f"http://127.0.0.1:{runner.addresses[0][1]}/mcp"
            if http:
                yield schema.HttpMcpServer(
                    name=_SERVER_NAME,
                    url=url,
                    headers=[schema.HttpHeader(name="Authorization", value=f"Bearer {token}")],
                )
            else:
                yield schema.McpServerStdio(
                    name=_SERVER_NAME,
                    command=sys.executable,
                    args=["-m", "a13n_harness.acp._stdio"],
                    env=[
                        schema.EnvVariable(name=_STDIO_URL, value=url),
                        schema.EnvVariable(name=_STDIO_TOKEN, value=token),
                    ],
                )
        finally:
            await runner.cleanup()

    async def _http_request(self, request: web.Request) -> web.Response:
        if "Origin" in request.headers:
            raise web.HTTPForbidden()
        provided = request.headers.get("Authorization", "").encode()
        if self._token is None or not secrets.compare_digest(provided, f"Bearer {self._token}".encode()):
            raise web.HTTPUnauthorized()
        turn_id = self._turn_id
        try:
            message = await request.json()
        except ValueError:
            return _rpc_error(None, -32700, "Parse error")
        if (
            not isinstance(message, dict)
            or message.get("jsonrpc") != "2.0"
            or not isinstance(message.get("method"), str)
        ):
            return _rpc_error(None, -32600, "Invalid request")
        request_id = message.get("id")
        if "id" not in message:
            return web.Response(status=202)
        params = message.get("params", {})
        if not isinstance(params, dict):
            return _rpc_error(request_id, -32602, "Invalid params")
        try:
            if message["method"] == "tools/call" and self._turn_id != turn_id:
                raise RequestError.invalid_request({"code": "acp_run_inactive"})
            result = await self._dispatch(message["method"], params)
        except RequestError as exc:
            return _rpc_error(request_id, exc.code, str(exc), exc.data)
        return web.json_response({"jsonrpc": "2.0", "id": request_id, "result": result})


def _rpc_error(request_id: Any, code: int, message: str, data: Any = None) -> web.Response:
    return web.json_response(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message, "data": data}}
    )
