"""Apps capture consumes the Host-owned MCP runtime without owning its lifecycle."""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import uuid4

from a13n_harness.context import AgentContext
from fastmcp.client.progress import ProgressHandler
from mcp.client.extension import advertise
from mcp.types import CallToolResult, Tool
from pydantic_ai import RunContext, ToolDefinition
from pydantic_ai.capabilities import MCP, ValidatedToolArgs, WrapToolExecuteHandler
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import ToolsetTool

from a13n_harness_ui.mcp_runtime.connections import Connection, HostClient, InputHandler
from a13n_harness_ui.mcp_runtime.connections import Connections as HostConnections
from a13n_harness_ui.mcp_runtime.projection import HostToolset

MIME_TYPE = "text/html;profile=mcp-app"
_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_MAX_PENDING_CALLS = 128
_MAX_PENDING_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class CapturedCall:
    connection: Connection
    tool: Tool
    arguments: dict[str, Any]
    result: CallToolResult
    app_id: str = field(default_factory=lambda: f"app_{uuid4().hex}")


@dataclass
class CallCapture:
    tool: Tool
    captures: Captures
    run_id: str
    call_id: str | None


_capture: ContextVar[CallCapture | None] = ContextVar("mcp_app_capture", default=None)
_return_boundary: ContextVar[tuple[str, str] | None] = ContextVar("mcp_app_return_boundary", default=None)


class AppMCP(MCP[AgentContext]):
    """Associate nested dispatches with the outer, retained native tool return."""

    async def wrap_tool_execute(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        current = _return_boundary.get()
        if current is not None and current[0] == ctx.deps.run_id:
            return await handler(args)
        token = _return_boundary.set((ctx.deps.run_id, call.tool_call_id))
        try:
            return await handler(args)
        finally:
            _return_boundary.reset(token)


class AppClient(HostClient):
    """Capture at the terminal raw boundary; preserve upstream model conversion."""

    async def call_tool_mcp(
        self,
        name: str,
        arguments: dict[str, Any],
        progress_handler: ProgressHandler | None = None,
        timeout: timedelta | float | int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult:
        result = await super().call_tool_mcp(name, arguments, progress_handler, timeout, meta)
        capture = _capture.get()
        if capture is not None:
            # Presentation failure cannot retry a completed business operation.
            try:
                capture.captures.add(
                    capture.run_id,
                    capture.call_id,
                    CapturedCall(self.connection, capture.tool, dict(arguments), result),
                )
            except (ValueError, TypeError):
                pass
        return result


class AppToolset(HostToolset):
    def __init__(self, connection: Connection, captures: Captures) -> None:
        super().__init__(connection)
        self.captures = captures

    async def list_tools(self) -> list[Tool]:
        tools = await super().list_tools()
        return [tool for tool in tools if "model" in visibility(tool)]

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        declaration = next((item for item in await self.client.list_tools() if item.name == name), None)
        if (tool.tool_def.metadata or {}).get("task"):
            raise ValueError("Task-based tools are not supported by this MCP Apps profile.")
        if declaration is not None:
            require_classic_tool(declaration)
        if declaration is None or resource_uri(declaration) is None:
            return await super().call_tool(name, tool_args, ctx, tool)
        boundary = _return_boundary.get()
        call_id = boundary[1] if boundary is not None and boundary[0] == ctx.deps.run_id else ctx.tool_call_id
        token = _capture.set(CallCapture(declaration, self.captures, ctx.deps.run_id, call_id))
        try:
            return await super().call_tool(name, tool_args, ctx, tool)
        finally:
            _capture.reset(token)


class Captures:
    """Short-lived raw data awaiting an actual retained tool-return boundary."""

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str], list[tuple[CapturedCall, int]]] = {}
        self._bytes = 0
        self._count = 0

    def add(self, run_id: str, call_id: str | None, call: CapturedCall) -> None:
        if not call_id:
            return
        size = len(call.result.model_dump_json().encode()) + len(call.tool.model_dump_json().encode())
        size += len(json.dumps(call.arguments).encode())
        if size > _MAX_CAPTURE_BYTES:
            return
        while self._pending and (self._count >= _MAX_PENDING_CALLS or self._bytes + size > _MAX_PENDING_BYTES):
            self.take(*next(iter(self._pending)))
        self._pending.setdefault((run_id, call_id), []).append((call, size))
        self._bytes += size
        self._count += 1

    def take(self, run_id: str, call_id: str) -> list[CapturedCall]:
        entries = self._pending.pop((run_id, call_id), [])
        self._bytes -= sum(size for _, size in entries)
        self._count -= len(entries)
        return [call for call, _ in entries]

    def discard_run(self, run_id: str) -> None:
        for key in tuple(self._pending):
            if key[0] == run_id:
                self.take(*key)


class Connections(HostConnections):
    """Configure Apps observation on the same generic ownership implementation."""

    def __init__(self, *, input_handler: InputHandler | None = None, cleanup_timeout_seconds: float = 10) -> None:
        super().__init__(
            client_factory=AppClient,
            extensions=[advertise("io.modelcontextprotocol/ui", {"mimeTypes": [MIME_TYPE]})],
            input_handler=input_handler,
            cleanup_timeout_seconds=cleanup_timeout_seconds,
        )
        self.captures = Captures()


def require_classic_tool(tool: Tool) -> None:
    if tool.execution is not None and tool.execution.task_support == "required":
        raise ValueError("Task-based tools are not supported by this MCP Apps profile.")


def resource_uri(tool: Tool) -> str | None:
    ui = (tool.meta or {}).get("ui")
    uri = ui.get("resourceUri") if isinstance(ui, dict) else None
    return uri if isinstance(uri, str) and uri.startswith("ui://") else None


def visibility(tool: Tool) -> list[str]:
    ui = (tool.meta or {}).get("ui")
    value = ui.get("visibility") if isinstance(ui, dict) else None
    return value if isinstance(value, list) else ["model", "app"]
