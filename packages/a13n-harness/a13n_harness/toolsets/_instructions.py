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
def tool_instruction(name: str, *sections: str) -> str:
    """Compose packaged Markdown sections under one tool instruction name."""
    prompts = files("a13n_harness.toolsets.prompts")
    content = "\n\n".join(
        prompts.joinpath(f"{section}.md").read_text(encoding="utf-8").strip() for section in (name, *sections)
    )
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
        if not ctx.deps.deferred_tools_supported or not ctx.deps.toolset_instructions or not self._instructions:
            return None
        return InstructionPart(content=self._instructions, dynamic=False)


__all__ = [
    "InstructionExternalToolset",
    "InstructionFunctionToolset",
    "tool_instruction",
]
