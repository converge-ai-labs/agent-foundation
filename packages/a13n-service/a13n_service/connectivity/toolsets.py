"""One fresh, source-bound MCP capability per external connection."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable, Sequence

from a13n_harness import AgentContext
from a13n_harness.tools import ToolIdentityToolset
from anyio import to_thread
from jsonschema import Draft202012Validator, ValidationError
from mcp.types import Tool
from pydantic import JsonValue
from pydantic_ai import RunContext
from pydantic_ai.capabilities import MCP
from pydantic_ai.tools import Tool as FunctionTool
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset, RenamedToolset, ToolsetTool

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
    return _PortableToolNames(ToolIdentityToolset(toolset, source_id=key, kind="mcp"), {}).prefixed(key)


def selected_tools(tools: Sequence[Tool], allowed: tuple[str, ...] | None) -> tuple[Tool, ...]:
    validate_tools(tools)
    by_name = {tool.name: tool for tool in tools}
    if allowed is not None and any(name not in by_name for name in allowed):
        raise ValueError("selected_tool_unavailable")
    return tuple(tool for tool in tools if allowed is None or tool.name in allowed)


def _bound_tool(definition: Tool, handler: ToolHandler) -> FunctionTool[AgentContext]:
    async def invoke(**arguments: JsonValue) -> JsonValue:
        try:
            await to_thread.run_sync(Draft202012Validator(definition.input_schema).validate, arguments)
        except ValidationError:
            raise ValueError("tool_arguments_invalid") from None
        result = await handler(definition.name, arguments)
        await to_thread.run_sync(validate_result, result)
        if definition.output_schema is not None:
            try:
                await to_thread.run_sync(Draft202012Validator(definition.output_schema).validate, result)
            except ValidationError:
                raise ValueError("tool_output_invalid") from None
        return result

    tool = FunctionTool[AgentContext].from_schema(
        invoke, name=definition.name, description=definition.description, json_schema=definition.input_schema
    )
    tool.metadata = {
        "meta": definition.meta,
        "annotations": definition.annotations.model_dump(by_alias=True) if definition.annotations else None,
        "task": False,
    }
    tool.function_schema.return_schema = definition.output_schema or {}
    return tool


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
    toolset = FunctionToolset[AgentContext]([_bound_tool(tool, handler) for tool in selected], id=key)
    # Upstream accepts AbstractToolset at runtime; its MCP constructor annotation is narrower.
    return MCP(local=namespaced(toolset, key), id=key, defer_loading=defer_loading)  # type: ignore[arg-type]
