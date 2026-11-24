"""Render Toolset guidance against the prepared tool surface."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from functools import cache, lru_cache
from importlib.resources import files
from typing import TYPE_CHECKING

from pydantic_ai.exceptions import ModelRetry, ToolRetryError
from pydantic_ai.messages import InstructionPart

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from pydantic_ai.toolsets import AbstractToolset, ToolsetTool

    from a13n_harness.context import AgentContext

__all__ = ["GuidanceFragment", "ToolGuidance"]

TOOL_GUIDANCE_KEY = "a13n.guidance"

REFERENCE_MARKER = "<!-- reference -->"


@dataclass(frozen=True, slots=True, kw_only=True)
class GuidanceFragment:
    """Plain Markdown and its tool requirements; both nonempty conditions must pass."""

    text: str
    any_of: tuple[str, ...] = ()
    all_of: tuple[str, ...] = ()
    reference: bool = False


@dataclass(frozen=True, slots=True, eq=False)
class ToolGuidance:
    """Reusable guidance for a Toolset's ``get_instructions`` hook."""

    name: str | None
    fragments: tuple[GuidanceFragment, ...]
    reference_page: str | None = None
    referenced_tools: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "referenced_tools", frozenset(name for part in self.fragments for name in part.any_of + part.all_of)
        )

    def render(
        self,
        ctx: RunContext[AgentContext],
        *,
        toolset: AbstractToolset[AgentContext],
        fallback: Iterable[str] = (),
    ) -> InstructionPart | None:
        """Render for the given owner; use fallback names only without a prepared inventory."""
        return self._for_names(ctx, _tool_names(ctx, (toolset,), fallback))

    def _for_names(self, ctx: RunContext[AgentContext], names: frozenset[str]) -> InstructionPart | None:
        if not ctx.deps.toolset_instructions or not names:
            return None
        root = _reference_root(ctx) if self.reference_page is not None else None
        content = _render_tool_guidance(self, names & self.referenced_tools, root)
        return InstructionPart(content=content, dynamic=False) if content is not None else None


def _reference_root(ctx: RunContext[AgentContext]) -> str | None:
    root = ctx.deps.instruction_reference_root
    if root is None:
        return None
    manager = getattr(ctx, "tool_manager", None)
    if manager is not None and manager.tools is not None and "view" not in manager.tools:
        # The Host configured a handbook, but this step cannot read it with view.
        return None
    return root


@lru_cache(maxsize=256)
def _render_tool_guidance(guide: ToolGuidance, names: frozenset[str], reference_root: str | None) -> str | None:
    inline: list[str] = []
    details: list[str] = []
    for part in guide.fragments:
        if (not part.any_of or not names.isdisjoint(part.any_of)) and names.issuperset(part.all_of):
            if text := part.text.strip():
                (details if part.reference else inline).append(text)
    if details and reference_root is not None and guide.reference_page is not None:
        inline.append(_reference(f"{reference_root}/{guide.reference_page}"))
    else:
        inline.extend(details)
    if not inline:
        return None
    body = "\n\n".join(inline)
    return f"<tool-name>{guide.name}</tool-name>\n{body}" if guide.name is not None else body


def _source_chain(tool: ToolsetTool[AgentContext]) -> Iterator[ToolsetTool[AgentContext]]:
    from a13n_harness.tools._retained_toolsets import _GenerationTool
    from a13n_harness.toolsets.codeact import CodeActSourceToolCarrier

    while True:
        yield tool
        if isinstance(tool, _GenerationTool):
            tool = tool.source
        elif isinstance(tool, CodeActSourceToolCarrier):
            tool = tool.source_tool
        else:
            return


def _tool_names(
    ctx: RunContext[AgentContext],
    owners: tuple[AbstractToolset[AgentContext], ...],
    fallback: Iterable[str] = (),
    *,
    direct_only: bool = False,
) -> frozenset[str]:
    from a13n_harness.tools.tool_search import proxy_membership

    # A few embedders call the hook with a lightweight, unprepared context.
    manager = getattr(ctx, "tool_manager", None)
    tools = manager.tools if manager is not None else None
    if tools is None:
        return frozenset(fallback)
    names: set[str] = set()
    for name, tool in tools.items():
        member = proxy_membership(tool.tool_def)
        if member is not None and direct_only:
            continue
        if any(_belongs_to(source, owners) for source in _source_chain(tool)):
            names.add(member.tool if member is not None else name)
    return frozenset(names)


def _belongs_to(tool: ToolsetTool[AgentContext], owners: tuple[AbstractToolset[AgentContext], ...]) -> bool:
    from pydantic_ai.toolsets import FunctionToolset, PrefixedToolset, RenamedToolset
    from pydantic_ai.toolsets.function import FunctionToolsetTool

    if any(tool.toolset is owner for owner in owners):
        return True
    if not isinstance(tool.toolset, PrefixedToolset | RenamedToolset):
        return False
    # Native name wrappers replace the owner on the prepared tool. Recover the
    # owner without undoing the name change or asking the Toolset to prepare again.
    leaves: list[AbstractToolset[AgentContext]] = []
    tool.toolset.apply(leaves.append)
    if len(leaves) == 1:
        return any(leaves[0] is owner for owner in owners)
    if isinstance(tool, FunctionToolsetTool) and tool.original_name is not None:
        return any(
            isinstance(leaf, FunctionToolset)
            and any(leaf is owner for owner in owners)
            and (original := leaf.tools.get(tool.original_name)) is not None
            and original.function_schema.call == tool.call_func
            for leaf in leaves
        )
    return False


@cache
def load_guidance(page: str) -> str:
    """Load one release-owned page; an invalid declaration fails at its source."""
    return files("a13n_harness.toolsets.prompts").joinpath("guidance", f"{page}.md").read_text(encoding="utf-8")


def guidance_has_reference(page: str) -> bool:
    return REFERENCE_MARKER in load_guidance(page)


def guidance_reference_path(reference_root: str, page: str) -> str:
    return f"{reference_root}/harness/{page}.md"


def _reference(path: str) -> str:
    return f"<reference>\nWorkflows and troubleshooting: `{path}`. Read it with view before a multi-step task or after an error.\n</reference>"


def render_guidance(content: str, reference_path: str | None = None) -> str:
    """Keep essential rules inline and optionally reference the remaining Markdown."""
    essentials, marker, details = content.partition(REFERENCE_MARKER)
    if reference_path is not None and marker and details.strip():
        return "\n\n".join(filter(None, (essentials.strip(), _reference(reference_path))))
    return (essentials + details).strip()


class _GuidanceRetryText(str):
    """Carry provenance through native validation wrapping without changing retry text."""

    page: str

    def __new__(cls, text: str, page: str) -> _GuidanceRetryText:
        value = super().__new__(cls, text)
        value.page = page
        return value

    def __reduce__(self) -> tuple[type[_GuidanceRetryText], tuple[str, str]]:
        return type(self), (str(self), self.page)


def retry_guidance_page(error: BaseException) -> str | None:
    """Recognize only boundary-owned retry text, never tool-authored hint strings."""
    content = (
        error.message
        if isinstance(error, ModelRetry)
        else error.tool_retry.content
        if isinstance(error, ToolRetryError)
        else None
    )
    return content.page if isinstance(content, _GuidanceRetryText) else None


def guidance_hint(reference_root: str, page: str) -> str:
    return (
        f"Guidance: `{guidance_reference_path(reference_root, page)}`. Read with view before deciding whether to retry."
    )


def hinted_retry_text(content: object, *, reference_root: str | None, page: object) -> str | None:
    """Append guidance once, only for reference-bearing pages in reference delivery."""
    if (
        reference_root is None
        or not isinstance(page, str)
        or not isinstance(content, str)
        or isinstance(content, _GuidanceRetryText)
        or not guidance_has_reference(page)
    ):
        return None
    return _GuidanceRetryText(content + "\n" + guidance_hint(reference_root, page), page)
