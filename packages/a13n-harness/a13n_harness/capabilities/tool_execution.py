"""Optional retained-tool wait policy; execution controls are builder-owned."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability

from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.tool_execution import ToolExecutionConfig as ToolExecutionConfig

TOOL_EXECUTION_CAPABILITY_ID = "a13n.tool-execution"


@dataclass(init=False)
class ToolExecutionCapability(AbstractCapability[AgentContext]):
    id = TOOL_EXECUTION_CAPABILITY_ID

    def __init__(self, config: ToolExecutionConfig | None = None) -> None:
        self.config = config or ToolExecutionConfig()

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        if self.config.mode == "auto" and ctx.deps.tool_execution is None:
            raise DefinitionError(
                "Automatic tool execution requires RunBindings.tool_execution.", code="tool_execution_unbound"
            )
        ctx.deps._tool_executions.config = self.config
        ctx.deps._tool_executions.enabled = ctx.deps.tool_execution is not None
        return self
