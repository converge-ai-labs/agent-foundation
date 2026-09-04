"""One fresh, source-bound MCP capability per external connection."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from a13n_harness import AgentContext
from anyio import to_thread
from fastmcp import FastMCP
from fastmcp.tools import Tool as LocalTool
from fastmcp.tools import ToolResult
from jsonschema import Draft202012Validator
from mcp.types import Tool
from pydantic import Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset, RenamedToolset, ToolsetTool

from .domain import JsonObject
from .tool_validation import validate_result, validate_tools

ToolHandler = Callable[[str, JsonObject], Awaitable[JsonValue]]


def source_key(kind: str, identifier: str) -> str:
    return f"{kind}_{hashlib.sha256(identifier.encode()).hexdigest()[:16]}"


def portable_tool_name(name: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:20]
    return f"{stem}_{hashlib.sha256(name.encode()).hexdigest()[:16]}"


class _PortableToolNames(RenamedToolset[AgentContext]):
    """Refresh deterministic aliases while upstream owns reverse mapping and dispatch."""

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        names = {portable_tool_name(name): name for name in tools}
        if len(names) != len(tools):
            raise ValueError("tool_namespace_collision")
        self.name_map = names
        renamed = await super().get_tools(ctx)
        if set(renamed) != set(names):
            raise ValueError("tool_discovery_changed")
        return renamed


def namespaced(toolset: AbstractToolset[AgentContext], key: str) -> AbstractToolset[AgentContext]:
    return _PortableToolNames(toolset, {}).prefixed(key)


def selected_tools(tools: Sequence[Tool], allowed: tuple[str, ...] | None) -> tuple[Tool, ...]:
    validate_tools(tools)
    by_name = {tool.name: tool for tool in tools}
    if allowed is not None and any(name not in by_name for name in allowed):
        raise ValueError("selected_tool_unavailable")
    return tuple(tool for tool in tools if allowed is None or tool.name in allowed)


class _BoundTool(LocalTool):
    handler: ToolHandler = Field(exclude=True, repr=False)

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        await to_thread.run_sync(Draft202012Validator(self.parameters).validate, arguments)
        result = await self.handler(self.name, arguments)
        await to_thread.run_sync(validate_result, result)
        if self.output_schema is not None:
            await to_thread.run_sync(Draft202012Validator(self.output_schema).validate, result)
        return ToolResult(content=result, structured_content=result if isinstance(result, dict) else None)


async def local_capability(
    *,
    key: str,
    tools: Sequence[Tool],
    allowed: tuple[str, ...] | None,
    handler: ToolHandler,
    defer_loading: bool = False,
) -> MCP[AgentContext] | None:
    selected = await to_thread.run_sync(selected_tools, tools, allowed)
    if not selected:
        return None
    server = FastMCP(key)
    for tool in selected:
        server.add_tool(
            _BoundTool(
                name=tool.name,
                description=tool.description,
                parameters=tool.inputSchema,
                output_schema=tool.outputSchema,
                annotations=tool.annotations,
                handler=handler,
            )
        )
    toolset = MCPToolset[AgentContext](server, id=key, tool_error_behavior="error")
    return MCP(local=namespaced(toolset, key), id=key, defer_loading=defer_loading)  # type: ignore[arg-type]
