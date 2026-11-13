"""UI-owned child messaging, dynamic creation, and parent runtime reminders."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import TYPE_CHECKING, Annotated, Any

from a13n_harness import AgentContext
from a13n_harness.capabilities import SubagentOperatorContext
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextBlock,
    ModelContextNext,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
)
from pydantic import Field
from pydantic_ai import FunctionToolset, RunContext, Tool

from a13n_harness_ui.model_thinking import ThinkingSelection

if TYPE_CHECKING:
    from a13n_harness_ui.composition import ResolvedRunComposition
    from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator


def guidance(name: str) -> str:
    return files("a13n_harness_ui.subagents").joinpath(f"{name}-guidance.md").read_text(encoding="utf-8").strip()


class SubagentTools(AbstractModelContextCapability):
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
        if self.subagent:
            tools.append(self.send_parent_message)
        elif self.composition.subagents.dynamic.enabled:
            tools.append(Tool(self.spawn_subagent, description=self._spawn_description()))
        if self.has_children:
            instructions.append(guidance("parent"))
        return FunctionToolset(tools, id=self.id, instructions="\n\n".join(instructions))

    def _spawn_description(self) -> str:
        policy = self.composition.subagents.dynamic
        description = "Start a task-specific leaf subagent. Return its execution receipt after admission."
        if not policy.allowed_models:
            return f"{description} Omit model; the subagent uses your Model."
        omitted = policy.default_model or "your Model"
        return f"{description} model accepts {', '.join(policy.allowed_models)}; when omitted, it uses {omitted}."

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

    async def send_parent_message(
        self, ctx: RunContext[AgentContext], message: Annotated[str, Field(min_length=1, max_length=1024 * 1024)]
    ) -> dict[str, bool]:
        """Send one message to the main agent and continue without waiting for a reply."""
        execution_id = ctx.deps.instance.delegation_id
        assert execution_id is not None
        return await self.operator.send_parent_message(execution_id, message)

    async def wrap_model_context(
        self,
        ctx: RunContext[AgentContext],
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        projection = await handler(request)
        if not self.has_children:
            return projection
        records = await self.operator.reminder(ctx.deps.thread_id)
        if not records:
            return projection
        block = ModelContextBlock(
            source_id="a13n-harness-ui.subagents.summary",
            context_group="runtime_state",
            placement=ModelContextPlacement.REQUEST_EPILOGUE,
            content="Check outstanding subagent work before reporting completion. "
            + "Final results arrive automatically; do not poll only to get them."
            + "\n"
            + json.dumps(records[:20], ensure_ascii=False)
            + ("\nMore executions are available through subagent_info." if len(records) > 20 else ""),
        )
        if request.history.matches_latest(block):
            return projection
        return ModelContextProjection(blocks=(*projection.blocks, block))
