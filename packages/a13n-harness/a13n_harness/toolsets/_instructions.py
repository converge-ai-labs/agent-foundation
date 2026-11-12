"""Shared loading and runtime gating for static Toolset guidance."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from functools import cache
from importlib.resources import files

from pydantic_ai import RunContext
from pydantic_ai.messages import InstructionPart
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import ExternalToolset, FunctionToolset

from a13n_harness.context import AgentContext
from a13n_harness.guidance import render_guidance

type ToolInstruction = str | Callable[[RunContext[AgentContext]], Awaitable[InstructionPart]]


@cache
def tool_instruction(name: str, *, reference_root: str | None = None) -> str:
    """Load one release-owned guidance file for a Toolset."""
    content = render_guidance(
        files("a13n_harness.toolsets.prompts").joinpath("guidance", f"{name}.md").read_text(encoding="utf-8"),
        f"{reference_root}/harness/{name}.md" if reference_root is not None else None,
    )
    return f"<tool-name>{name}</tool-name>\n{content}"


def tool_guidance(name: str) -> Callable[[RunContext[AgentContext]], Awaitable[InstructionPart]]:
    """Resolve delivery from fresh Run bindings without changing tool selection."""

    async def instructions(ctx: RunContext[AgentContext]) -> InstructionPart:
        return InstructionPart(
            content=tool_instruction(name, reference_root=ctx.deps.instruction_reference_root),
            dynamic=False,
        )

    return instructions


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
        instructions: ToolInstruction | None = None,
    ) -> None:
        super().__init__(tool_defs, id=id)
        self._instructions = instructions

    async def get_instructions(
        self,
        ctx: RunContext[AgentContext],
    ) -> str | InstructionPart | Sequence[str | InstructionPart] | None:
        if not ctx.deps.deferred_tools_supported or not ctx.deps.toolset_instructions or not self._instructions:
            return None
        if callable(self._instructions):
            return await self._instructions(ctx)
        return InstructionPart(content=self._instructions, dynamic=False)


__all__ = [
    "InstructionExternalToolset",
    "InstructionFunctionToolset",
    "render_guidance",
    "tool_guidance",
    "tool_instruction",
]
