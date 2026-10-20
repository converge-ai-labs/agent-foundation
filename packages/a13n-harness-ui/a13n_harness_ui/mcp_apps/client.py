"""MCP Apps SDK capture and Toolset adapters."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from a13n_harness.context import AgentContext
from fastmcp.client.progress import ProgressHandler
from mcp.types import CallToolResult, Tool
from pydantic_ai import RunContext
from pydantic_ai.toolsets import ToolsetTool

from a13n_harness_ui.mcp_runtime.client import HostClient
from a13n_harness_ui.mcp_runtime.projection import HostToolset

if TYPE_CHECKING:
    from a13n_harness_ui.mcp_runtime.connections import Connection

    from .connections import Captures


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
        from .connections import CapturedCall, _capture

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
        from .connections import visibility

        tools = await super().list_tools()
        return [tool for tool in tools if "model" in visibility(tool)]

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        from .connections import CallCapture, _capture, _return_boundary, require_classic_tool, resource_uri

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
