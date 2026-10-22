"""The tool-source contract: what discovery reports and the host check every tool call passes before dispatch.

A tool source receives plain values (a URL, a revealed credential, an entered connector runtime) and returns
Harness capabilities; it never reads business tables. Tools reach a model and calls leave the worker only
through `CheckedToolset`, so a refused check means the call was never sent.

Discovery reads up to the Harness directory bound, so a subset of a large server or app can be chosen; what
one connection exposes to a model stays within `MAX_TOOLS`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from a13n_harness import AgentContext, DefinitionError
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.toolsets import ToolsetTool, WrapperToolset

# Tools one connection exposes to a model, and an agent may select from it: every definition costs context.
MAX_TOOLS = 128


def unique[T](values: tuple[T, ...]) -> tuple[T, ...]:
    """Names, such as tools or headers, each listed at most once."""
    if len(set(values)) != len(values):
        raise ValueError("Values must be unique")
    return values


class ToolInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    description: str | None
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue] | None = None
    # Hints the server declares, such as `readOnlyHint`; shown to people, never trusted as a permission.
    annotations: dict[str, JsonValue] = Field(default_factory=dict)
    # MCP declaration metadata, including App resource URI and visibility.
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    # A connector's catalogue version the tool is executed at; None for MCP.
    provider_version: str | None = None


@dataclass(frozen=True, slots=True)
class ToolDispatch:
    """One tool call about to be sent; `tool_call_id` is the model's identity for the call."""

    connection_id: str
    # The connector provider serving the call; None for a remote MCP server.
    provider_id: str | None
    tool_name: str
    tool_call_id: str


# Raises to refuse the call; runs before every dispatch, including each retry the model makes.
type DispatchCheck = Callable[[ToolDispatch], Awaitable[None]]


@dataclass
class CheckedToolset(WrapperToolset[AgentContext]):
    connection_id: str
    provider_id: str | None
    check: DispatchCheck

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await super().get_tools(ctx)
        if len(tools) > MAX_TOOLS:
            raise DefinitionError(
                f"Connection {self.connection_id} exposes more than {MAX_TOOLS} tools; select the tools it offers.",
                code="connection_tools_exceeded",
            )
        return tools

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        if ctx.tool_call_id is None:
            raise ToolFailed("The tool call has no call identity and was not sent.")
        await self.check(ToolDispatch(self.connection_id, self.provider_id, name, ctx.tool_call_id))
        return await self.wrapped.call_tool(name, tool_args, ctx, tool)
