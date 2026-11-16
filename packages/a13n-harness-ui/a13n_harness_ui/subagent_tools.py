"""UI-owned dynamic child creation and collaboration guidance."""

from __future__ import annotations

from importlib.resources import files
from typing import TYPE_CHECKING, Annotated, Any

from a13n_harness import AgentContext
from a13n_harness.capabilities import SubagentOperatorContext
from pydantic import Field
from pydantic_ai import FunctionToolset, RunContext, Tool
from pydantic_ai.capabilities import AbstractCapability

from a13n_harness_ui.model_thinking import ThinkingSelection

if TYPE_CHECKING:
    from a13n_harness_ui.composition import ResolvedRunComposition
    from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator


def guidance(name: str) -> str:
    return files("a13n_harness_ui.subagents").joinpath(f"{name}-guidance.md").read_text(encoding="utf-8").strip()


class SubagentTools(AbstractCapability[AgentContext]):
    id = "a13n-harness-ui.subagent-tools"

    def __init__(
        self,
        operator: HarnessUiSubagentOperator,
        composition: ResolvedRunComposition,
        *,
        subagent: bool,
        has_children: bool,
    ) -> None:
        self.operator = operator
        self.composition = composition
        self.subagent = subagent
        self.has_children = has_children

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        tools = []
        instructions = []
        if not self.subagent and self.composition.subagents.dynamic.enabled:
            tools.append(Tool(self.spawn_subagent, description=self._spawn_description()))
        if self.has_children:
            instructions.append(guidance("parent"))
        return FunctionToolset(tools, id=self.id, instructions="\n\n".join(instructions))

    def _spawn_description(self) -> str:
        policy = self.composition.subagents.dynamic
        description = "Start a task-specific leaf subagent. Return its execution receipt after admission."
        models = ", ".join(policy.allowed_models)
        return f"{description} model accepts {models}; when omitted, it uses {policy.default_model}."

    async def spawn_subagent(
        self,
        ctx: RunContext[AgentContext],
        name: Annotated[str, Field(min_length=1, max_length=63)],
        instructions: Annotated[str, Field(min_length=1, max_length=1024 * 1024)],
        prompt: Annotated[str, Field(min_length=1, max_length=1024 * 1024)],
        model: str | None = None,
        thinking: ThinkingSelection | None = None,
    ) -> Any:
        context = SubagentOperatorContext(
            parent_thread_id=ctx.deps.thread_id,
            parent_run_id=ctx.deps.run_id,
            parent_agent_instance_id=ctx.deps.instance.agent_instance_id,
            host_refs=ctx.deps.instance.host_refs,
        )
        return (
            await self.operator.spawn(
                context,
                name=name,
                instructions=instructions,
                prompt=prompt,
                model=model,
                thinking=thinking,
            )
        ).model_dump(mode="json")
