"""Retain dynamic Toolset generations without changing native factory scheduling."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field, replace
from typing import Any, Self

from pydantic_ai import RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import AbstractToolset, DynamicToolset, ToolsetTool, WrapperToolset

from a13n_harness._retained_scope import RetainedScope
from a13n_harness._tool_execution import TOOL_EXECUTION_METADATA_KEY
from a13n_harness.context import AgentContext

_SCOPE_METADATA_KEY = "a13n.execution-resource-scopes"


@dataclass(kw_only=True)
class _GenerationTool(ToolsetTool[AgentContext]):
    source: ToolsetTool[AgentContext]
    generation: _Generation


@dataclass
class _Generation(WrapperToolset[AgentContext]):
    _scope: RetainedScope | None = field(default=None, repr=False)

    async def __aenter__(self) -> Self:
        self._scope = RetainedScope(self.wrapped)
        await self._scope.ready()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._scope is not None:
            await self._scope.close()

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        assert self._scope is not None
        result: dict[str, ToolsetTool[AgentContext]] = {}
        for name, tool in tools.items():
            metadata = dict(tool.tool_def.metadata or {})
            metadata[_SCOPE_METADATA_KEY] = (*metadata.get(_SCOPE_METADATA_KEY, ()), self._scope)
            result[name] = _GenerationTool(
                toolset=tool.toolset,
                tool_def=replace(tool.tool_def, metadata=metadata),
                max_retries=tool.max_retries,
                args_validator=tool.args_validator,
                args_validator_func=tool.args_validator_func,
                source=tool,
                generation=self,
            )
        return result


@dataclass
class _DynamicBoundary(WrapperToolset[AgentContext]):
    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        # Native DynamicToolset routes through its current generation, which can
        # change while CodeAct still owns an earlier prepared ToolManager.
        assert isinstance(tool, _GenerationTool)
        return await tool.generation.wrapped.call_tool(
            name, tool_args, ctx, replace(tool.source, tool_def=tool.tool_def)
        )


def retain_dynamic_toolsets(toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
    def visit(child: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        if isinstance(child, MCPToolset):
            return child.with_metadata(**{TOOL_EXECUTION_METADATA_KEY: "owned"})
        if not isinstance(child, DynamicToolset):
            return child
        factory = child.toolset_func
        previous: AbstractToolset[AgentContext] | None = None
        retained: _Generation | None = None

        async def retained_factory(ctx: RunContext[AgentContext]) -> AbstractToolset[AgentContext] | None:
            nonlocal previous, retained
            generated = factory(ctx)
            if inspect.isawaitable(generated):
                generated = await generated
            if generated is not previous:
                previous = generated
                retained = _Generation(retain_dynamic_toolsets(generated)) if generated is not None else None
            return retained

        return _DynamicBoundary(DynamicToolset(retained_factory, per_run_step=child.per_run_step, id=child.id))

    return toolset.visit_and_replace(visit)


def invocation_scopes(tool: ToolsetTool[AgentContext], ctx: RunContext[AgentContext]) -> tuple[RetainedScope, ...]:
    tools = (tool,)
    if (tool.tool_def.metadata or {}).get("a13n.codeact.runner") is True and ctx.tool_manager is not None:
        tools = tuple((ctx.tool_manager.tools or {}).values())
    return tuple(
        dict.fromkeys(
            scope for entry in tools for scope in (entry.tool_def.metadata or {}).get(_SCOPE_METADATA_KEY, ())
        )
    )
