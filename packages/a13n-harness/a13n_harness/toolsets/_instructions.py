"""Shared loading and runtime gating for static Toolset guidance."""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache
from importlib.resources import files

from pydantic_ai import RunContext
from pydantic_ai.messages import InstructionPart
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import ExternalToolset, FunctionToolset

from a13n_harness.context import AgentContext


@cache
def tool_instruction(name: str) -> str:
    """Load and wrap one packaged Markdown instruction block."""
    content = files("a13n_harness.toolsets.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")
    return f'<tool-instruction name="{name}">\n{content.strip()}\n</tool-instruction>'


class InstructionFunctionToolset(FunctionToolset[AgentContext]):
    """Function Toolset whose static guidance honors the effective run switch."""

    async def get_instructions(self, ctx: RunContext[AgentContext]) -> list[InstructionPart] | None:
        if not ctx.deps.toolset_instructions:
            return None
        return await super().get_instructions(ctx)


class InstructionExternalToolset(ExternalToolset[AgentContext]):
    """External Toolset whose static guidance honors the effective run switch."""

    def __init__(
        self,
        tool_defs: list[ToolDefinition],
        *,
        id: str | None = None,
        instructions: str | None = None,
    ) -> None:
        super().__init__(tool_defs, id=id)
        self._instructions = instructions

    async def get_instructions(
        self,
        ctx: RunContext[AgentContext],
    ) -> str | InstructionPart | Sequence[str | InstructionPart] | None:
        if (
            ctx.deps.instance.parent_agent_instance_id is not None
            or not ctx.deps.toolset_instructions
            or not self._instructions
        ):
            return None
        return InstructionPart(content=self._instructions, dynamic=False)


__all__ = [
    "InstructionExternalToolset",
    "InstructionFunctionToolset",
    "tool_instruction",
]
