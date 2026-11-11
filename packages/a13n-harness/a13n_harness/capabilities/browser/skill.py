"""Release-owned browser skills exposed through the Host's read-only Environment mount."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset, ToolsetTool, WrapperToolset

from a13n_harness.capabilities.skills import FileSkillSource
from a13n_harness.context import AgentContext
from a13n_harness.tools.surface import ToolSurfaceCapability

type BrowserSkillName = Literal["browser-use", "browser-use-code"]
BROWSER_SKILLS_ROOT = Path(__file__).with_name("skills")
BROWSER_SKILLS_PATH = "/environment/browser-skills"


def browser_skill_source(name: BrowserSkillName) -> FileSkillSource:
    if name not in {"browser-use", "browser-use-code"}:
        raise ValueError("Unknown built-in browser skill.")
    return FileSkillSource("a13n-browser-skills", (f"{BROWSER_SKILLS_PATH}/{name}",), skip_invalid=False)


def _reader(name: BrowserSkillName) -> FunctionToolset[AgentContext]:
    """Only for a Host with no general file viewer. Grant access to one selected release document."""
    path = f"{BROWSER_SKILLS_PATH}/{name}/SKILL.md"
    tools: FunctionToolset[AgentContext] = FunctionToolset()

    @tools.tool
    async def view(ctx: RunContext[AgentContext], file_path: str) -> str:
        """Read the selected built-in browser skill by its catalog path."""
        if file_path != path:
            raise ModelRetry("Use the exact path of this Run's browser skill.")
        return (await ctx.deps.environment.files.read_text(path, line_limit=300)).text

    return tools


@dataclass
class BrowserSkillReader(AbstractCapability[AgentContext]):
    """Make the manual readable even for a browser-only agent."""

    skill_name: BrowserSkillName
    id = "a13n.browser-skill-reader"

    def __post_init__(self) -> None:
        if self.skill_name not in {"browser-use", "browser-use-code"}:
            raise ValueError("Unknown built-in browser skill.")

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wrapped_by=(ToolSurfaceCapability,))

    def get_wrapper_toolset(self, toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        return _WithSkillReader(toolset, _reader(self.skill_name))


@dataclass
class _WithSkillReader(WrapperToolset[AgentContext]):
    reader: FunctionToolset[AgentContext]

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        if "view" in tools:
            return tools
        return {**tools, **await self.reader.get_tools(ctx)}

    async def call_tool(
        self, name: str, tool_args: dict[str, Any], ctx: RunContext[AgentContext], tool: ToolsetTool[AgentContext]
    ) -> Any:
        if tool.toolset is self.reader:
            return await self.reader.call_tool(name, tool_args, ctx, tool)
        return await self.wrapped.call_tool(name, tool_args, ctx, tool)
