"""Bind built-in guidance to its owning Toolsets."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from functools import cache
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import InstructionPart
from pydantic_ai.tools import Tool, ToolDefinition
from pydantic_ai.toolsets import ExternalToolset, FunctionToolset

from a13n_harness.context import AgentContext
from a13n_harness.guidance import (
    REFERENCE_MARKER,
    GuidanceFragment,
    ToolGuidance,
    _tool_names,
    guidance_reference_path,
    load_guidance,
    render_guidance,
)


@cache
def tool_instruction(name: str, *, reference_root: str | None = None) -> str:
    """Load a release-owned guidance template, including tool descriptions."""
    content = render_guidance(
        load_guidance(name),
        guidance_reference_path(reference_root, name) if reference_root is not None else None,
    )
    return f"<tool-name>{name}</tool-name>\n{content}"


@cache
def tool_guidance(name: str, *fragments: GuidanceFragment) -> ToolGuidance:
    if not fragments:
        essentials, _, details = load_guidance(name).partition(REFERENCE_MARKER)
        fragments = (GuidanceFragment(text=essentials), GuidanceFragment(text=details, reference=True))
    return ToolGuidance(name, fragments, reference_page=f"harness/{name}.md")


def section(
    page: str, tag: str, *, any_of: tuple[str, ...] = (), all_of: tuple[str, ...] = (), reference: bool = False
) -> GuidanceFragment:
    """Select an existing presentation section from the static handbook."""
    essentials, _, details = load_guidance(page).partition(REFERENCE_MARKER)
    start, end = f"<{tag}>", f"</{tag}>"
    _, opened, rest = (details if reference else essentials).partition(start)
    content, closed, _ = rest.partition(end)
    if not opened or not closed:
        raise ValueError(f"Missing guidance section {page}/{tag}")
    return GuidanceFragment(text=start + content + end, any_of=any_of, all_of=all_of, reference=reference)


class InstructionFunctionToolset(FunctionToolset[AgentContext]):
    """Function Toolset with guidance gated by its surviving tools."""

    def __init__(
        self,
        *,
        tools: Sequence[Tool[AgentContext]] = (),
        id: str | None = None,
        metadata: dict[str, Any] | None = None,
        instructions: str | Callable[[RunContext[AgentContext]], Awaitable[InstructionPart]] | None = None,
        guidance: ToolGuidance | None = None,
        guidance_for: tuple[FunctionToolset[AgentContext], ...] = (),
    ) -> None:
        super().__init__(tools=tools or (), id=id, metadata=metadata, instructions=instructions)
        self._guidance = guidance
        self._guidance_for = guidance_for

    async def get_instructions(self, ctx: RunContext[AgentContext]) -> list[InstructionPart] | None:
        if not ctx.deps.toolset_instructions:
            return None
        owners = self._guidance_for or (self,)
        names = _tool_names(ctx, owners, (name for owner in owners for name in owner.tools))
        if not names:
            return None
        part = self._guidance._for_names(ctx, names) if self._guidance is not None else None
        extra = await super().get_instructions(ctx)
        return [part, *(extra or ())] if part is not None else extra


class InstructionExternalToolset(ExternalToolset[AgentContext]):
    """External Toolset with guidance gated by its surviving tools."""

    def __init__(
        self,
        tool_defs: list[ToolDefinition],
        *,
        id: str | None = None,
        guidance: ToolGuidance | None = None,
    ) -> None:
        super().__init__(tool_defs, id=id)
        self._guidance = guidance

    async def get_instructions(self, ctx: RunContext[AgentContext]) -> InstructionPart | None:
        if not ctx.deps.deferred_tools_supported or self._guidance is None:
            return None
        return self._guidance.render(ctx, toolset=self, fallback=(tool.name for tool in self.tool_defs))
