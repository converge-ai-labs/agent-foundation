"""Apps capture consumes the Host-owned MCP runtime without owning its lifecycle."""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from a13n_harness.context import AgentContext
from mcp.client.extension import advertise
from mcp.types import Tool
from pydantic import TypeAdapter
from pydantic_ai import RunContext, ToolDefinition, ToolReturn
from pydantic_ai.capabilities import MCP, ValidatedToolArgs, WrapToolExecuteHandler
from pydantic_ai.mcp import MCPAppToolResult
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import ToolsetTool

from a13n_harness_ui.mcp_runtime.connections import Connection, InputHandler
from a13n_harness_ui.mcp_runtime.connections import Connections as HostConnections
from a13n_harness_ui.mcp_runtime.projection import HostToolset

MIME_TYPE = "text/html;profile=mcp-app"
_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_MAX_PENDING_CALLS = 128
_MAX_PENDING_BYTES = 32 * 1024 * 1024
_APP_RESULT = TypeAdapter(MCPAppToolResult)


@dataclass(frozen=True)
class CapturedCall:
    connection: Connection
    tool: Tool
    arguments: dict[str, Any]
    result: dict[str, Any]
    app_id: str = field(default_factory=lambda: f"app_{uuid4().hex}")


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


class AppToolset(HostToolset):
    def __init__(self, connection: Connection, captures: Captures) -> None:
        super().__init__(connection)
        self.captures = captures

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        declaration = next((item for item in await self.client.list_tools() if item.name == name), None)
        if (tool.tool_def.metadata or {}).get("task"):
            raise ValueError("Task-based tools are not supported by this MCP Apps profile.")
        if declaration is not None:
            require_classic_tool(declaration)
        result = await super().call_tool(name, tool_args, ctx, tool)
        if not isinstance(result, ToolReturn) or not isinstance(result.metadata, dict):
            return result
        # Consume upstream presentation data before nested runners can discard
        # metadata. Only the immutable reference belongs in retained UI history.
        metadata = dict(result.metadata)
        payload = metadata.pop("mcp_app", None)
        result.metadata = metadata or None
        if payload is None or declaration is None or resource_uri(declaration) is None:
            return result
        boundary = _return_boundary.get()
        call_id = boundary[1] if boundary is not None and boundary[0] == ctx.deps.run_id else ctx.tool_call_id
        try:
            app = _APP_RESULT.validate_python(payload)
            if app["resource_uri"] == resource_uri(declaration):
                self.captures.add(
                    ctx.deps.run_id,
                    call_id,
                    CapturedCall(self.connection, declaration, app["tool_input"], app["result"]),
                )
        except (ValueError, TypeError):
            # Presentation failure cannot retry a completed business operation.
            pass
        return result


class Captures:
    """Bounded App payloads awaiting an actual retained tool-return boundary."""

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str], list[tuple[CapturedCall, int]]] = {}
        self._bytes = 0
        self._count = 0

    def add(self, run_id: str, call_id: str | None, call: CapturedCall) -> None:
        if not call_id:
            return
        size = len(json.dumps(call.result).encode()) + len(call.tool.model_dump_json().encode())
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

    def discard_run(self, run_id: str, *, retain: set[str] | None = None) -> None:
        for key in tuple(self._pending):
            if key[0] == run_id and (retain is None or key[1] not in retain):
                self.take(*key)


class Connections(HostConnections):
    """Configure Apps observation on the same generic ownership implementation."""

    def __init__(self, *, input_handler: InputHandler | None = None, cleanup_timeout_seconds: float = 10) -> None:
        super().__init__(
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
