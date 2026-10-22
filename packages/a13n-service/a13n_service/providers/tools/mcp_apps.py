"""Passive MCP Apps capture before the native adapter converts results for the model."""

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from a13n_harness import AgentContext
from a13n_logging import get_logger
from fastmcp import Client
from fastmcp.client.progress import ProgressHandler
from fastmcp.client.transports import ClientTransport
from mcp.types import CallToolResult, Tool
from pydantic_ai import RunContext, ToolDefinition, ToolReturn
from pydantic_ai.capabilities import MCP, ValidatedToolArgs, WrapToolExecuteHandler
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import ToolsetTool

MIME_TYPE = "text/html;profile=mcp-app"
logger = get_logger(__name__)


def resource_uri(tool: Tool) -> str | None:
    ui = (tool.meta or {}).get("ui")
    uri = ui.get("resourceUri") if isinstance(ui, dict) else None
    return uri if isinstance(uri, str) and uri.startswith("ui://") and len(uri) <= 2048 else None


def app_visible(tool: Tool) -> bool:
    ui = (tool.meta or {}).get("ui")
    visibility = ui.get("visibility") if isinstance(ui, dict) else None
    return not isinstance(visibility, list) or "app" in visibility


def require_classic(tool: Tool) -> None:
    if tool.execution is not None and tool.execution.task_support == "required":
        raise ToolFailed("Task-based MCP Apps tools are unsupported.")


@dataclass(frozen=True)
class Original:
    context: AgentContext
    tool_call_id: str
    tool: Tool
    arguments: dict[str, Any]
    result: CallToolResult


type Capture = Callable[[Original], None]

_capture: ContextVar[tuple[Capture, AgentContext, str, Tool] | None] = ContextVar("mcp_app_capture", default=None)
_boundary: ContextVar[tuple[str, str] | None] = ContextVar("mcp_app_return_boundary", default=None)


class AppClient(Client[ClientTransport]):
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
            sink, context, call_id, tool = capture
            try:
                sink(Original(context, call_id, tool, dict(arguments), result))
            except Exception:
                # The business call completed. A presentation failure must never cause its replay.
                logger.warning("MCP App capture failed", extra={"tool_call_id": call_id})
        return result


class AppToolset(MCPToolset[AgentContext]):
    capture: Capture | None = None

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        result = await self._capture_tool(name, tool_args, ctx, tool)
        if isinstance(result, ToolReturn) and isinstance(result.metadata, dict):
            # Upstream also retains the raw App payload. Service stores it separately;
            # only the opaque App reference may enter model history and display state.
            metadata = dict(result.metadata)
            metadata.pop("mcp_app", None)
            result.metadata = metadata or None
        return result

    async def _capture_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        meta = (tool.tool_def.metadata or {}).get("meta")
        ui = meta.get("ui") if isinstance(meta, dict) else None
        if self.capture is None or not isinstance(ui, dict) or not isinstance(ui.get("resourceUri"), str):
            return await super().call_tool(name, tool_args, ctx, tool)
        declaration = next((item for item in await self.list_tools() if item.name == name), None)
        if declaration is None or resource_uri(declaration) is None:
            return await super().call_tool(name, tool_args, ctx, tool)
        require_classic(declaration)
        boundary = _boundary.get()
        call_id = boundary[1] if boundary is not None and boundary[0] == ctx.deps.run_id else ctx.tool_call_id
        if call_id is None:
            raise ToolFailed("The MCP App call has no identity.")
        token = _capture.set((self.capture, ctx.deps, call_id, declaration))
        try:
            return await super().call_tool(name, tool_args, ctx, tool)
        finally:
            _capture.reset(token)


class AppMCP(MCP[AgentContext]):
    async def wrap_tool_execute(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        current = _boundary.get()
        if current is not None and current[0] == ctx.deps.run_id:
            return await handler(args)
        token = _boundary.set((ctx.deps.run_id, call.tool_call_id))
        try:
            return await handler(args)
        finally:
            _boundary.reset(token)
