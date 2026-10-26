"""Consume native MCP Apps results; retain raw errors before the adapter raises."""

from collections.abc import Callable
from contextvars import ContextVar
from copy import deepcopy
from datetime import timedelta
from typing import Any

from a13n_harness import AgentContext
from a13n_logging import get_logger
from fastmcp import Client
from fastmcp.client.progress import ProgressHandler
from fastmcp.client.transports import ClientTransport
from mcp.types import CallToolResult, Tool
from pydantic import TypeAdapter
from pydantic_ai import RunContext, ToolDefinition, ToolReturn
from pydantic_ai.capabilities import MCP, ValidatedToolArgs, WrapToolExecuteHandler
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.mcp import MCPAppToolResult, MCPToolset
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.toolsets import ToolsetTool

logger = get_logger(__name__)
_APP_RESULT = TypeAdapter(MCPAppToolResult)


def resource_uri(tool: Tool) -> str | None:
    ui = (tool.meta or {}).get("ui")
    uri = ui.get("resourceUri") if isinstance(ui, dict) else None
    return uri if isinstance(uri, str) and uri.startswith("ui://") and len(uri) <= 2048 else None


def require_classic(tool: Tool) -> None:
    if tool.execution is not None and tool.execution.task_support == "required":
        raise ToolFailed("Task-based MCP Apps tools are unsupported.")


type Capture = Callable[[AgentContext, str, Tool, MCPAppToolResult], None]

_error_capture: ContextVar[Callable[[str, CallToolResult], None] | None] = ContextVar(
    "mcp_app_error_capture", default=None
)
_boundary: ContextVar[tuple[str, str] | None] = ContextVar("mcp_app_return_boundary", default=None)


class AppClient(Client[ClientTransport]):
    """Successful Apps use upstream metadata; only errors need the raw client boundary."""

    async def call_tool_mcp(
        self,
        name: str,
        arguments: dict[str, Any],
        progress_handler: ProgressHandler | None = None,
        timeout: timedelta | float | int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult:
        result = await super().call_tool_mcp(name, arguments, progress_handler, timeout, meta)
        capture = _error_capture.get()
        if capture is not None and result.is_error:
            capture(name, result)
        return result


class AppToolset(MCPToolset[AgentContext]):
    capture: Capture | None = None

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        declaration = None
        meta = (tool.tool_def.metadata or {}).get("meta")
        ui = meta.get("ui") if isinstance(meta, dict) else None
        if self.capture is not None and isinstance(ui, dict) and isinstance(ui.get("resourceUri"), str):
            declaration = next((item for item in await self.list_tools() if item.name == name), None)
        if declaration is not None and resource_uri(declaration) is None:
            declaration = None
        if declaration is not None:
            require_classic(declaration)
        boundary = _boundary.get()
        call_id = boundary[1] if boundary is not None and boundary[0] == ctx.deps.run_id else ctx.tool_call_id
        if declaration is not None and call_id is None:
            raise ToolFailed("The MCP App call has no identity.")

        failed: CallToolResult | None = None
        arguments = deepcopy(tool_args) if declaration is not None else {}

        def remember_error(called_name: str, result: CallToolResult) -> None:
            nonlocal failed
            if called_name == name:
                failed = result

        token = _error_capture.set(remember_error if declaration is not None else None)
        try:
            result = await super().call_tool(name, tool_args, ctx, tool)
        except ToolFailed:
            if failed is not None and declaration is not None and call_id is not None:
                try:
                    self._retain(
                        ctx.deps,
                        call_id,
                        declaration,
                        MCPAppToolResult(
                            resource_uri=resource_uri(declaration) or "",
                            tool_input=arguments,
                            result=failed.model_dump(mode="json", by_alias=True, exclude_none=True),
                            toolset_id=self.id,
                        ),
                    )
                except Exception:
                    logger.warning("MCP App error capture failed", extra={"tool_call_id": call_id})
            raise
        finally:
            _error_capture.reset(token)
        if isinstance(result, ToolReturn) and isinstance(result.metadata, dict):
            # Consume the upstream App payload before nested tools can discard metadata;
            # only the opaque App reference may enter model history and display state.
            metadata = dict(result.metadata)
            payload = metadata.pop("mcp_app", None)
            result.metadata = metadata or None
            if payload is not None and declaration is not None and call_id is not None:
                self._retain(ctx.deps, call_id, declaration, payload)
        return result

    def _retain(self, context: AgentContext, call_id: str, tool: Tool, payload: Any) -> None:
        try:
            app = _APP_RESULT.validate_python(payload)
            if self.capture is not None and app["resource_uri"] == resource_uri(tool):
                self.capture(context, call_id, tool, app)
        except Exception:
            # The business call completed. A presentation failure must never cause its replay.
            logger.warning("MCP App capture failed", extra={"tool_call_id": call_id})


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
