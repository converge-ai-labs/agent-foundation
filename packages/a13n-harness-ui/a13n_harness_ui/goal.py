"""Root-only Goal continuation policy and checkpointed progress.

The Agent audits its own work. A completion marker is a protocol result, not an
independent verifier's certification. Ordinary output and native execution remain
owned by Harness and Pydantic AI.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from a13n_harness import HarnessState
from a13n_harness.capabilities.context import ContextRestoredEvent
from a13n_harness.context import AgentContext
from a13n_harness.state import AgentContextStateSnapshot, CapabilityState
from a13n_harness.usage import RunUsageSummary, UsageSnapshot
from anyio import to_thread
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import AgentStreamEvent, ModelRequest, TextContent, UserPromptPart
from pydantic_ai.models import ModelRequestContext

from a13n_harness_ui.goal_prompts import goal_check_prompt, has_completion_marker, post_restore_audit_prompt

GOAL_CAPABILITY_ID = "a13n.harness-ui.goal"
GOAL_STATE_VERSION = "1"
GoalMode = Literal["normal", "goal"]
GoalStatus = Literal[
    "working",
    "checking",
    "auditing",
    "suspended",
    "verified",
    "max_iterations",
    "cancelled",
    "error",
    "unverified_stop",
]


class GoalView(BaseModel):
    """Detached Goal progress; saved progress alone never proves live execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(min_length=1)
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = 10
    status: GoalStatus = "working"
    needs_restore_audit: bool = False
    restore_source: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    usage_id: str | None = None
    usage_input_base: int = Field(default=0, ge=0)
    usage_output_base: int = Field(default=0, ge=0)

    @property
    def active(self) -> bool:
        return self.status in {"working", "checking", "auditing", "suspended"}


def saved_goal(state: HarnessState) -> GoalView | None:
    entry = state.agent_context_state.get(GOAL_CAPABILITY_ID)
    if entry is None:
        return None
    if entry.version != GOAL_STATE_VERSION:
        raise ValueError("Unsupported Goal state version")
    return GoalView.model_validate(entry.data)


def with_goal(state: HarnessState, goal: GoalView | None) -> HarnessState:
    """Replace only the Goal namespace, preserving messages and other owners."""
    entries = state.agent_context_state.entries
    if goal is None:
        entries.pop(GOAL_CAPABILITY_ID, None)
    else:
        entries[GOAL_CAPABILITY_ID] = CapabilityState(version=GOAL_STATE_VERSION, data=goal.model_dump(mode="json"))
    return state.model_copy(update={"agent_context_state": AgentContextStateSnapshot(entries=entries)})


class GoalCapability(AbstractCapability[AgentContext]):
    """Enqueue native continuations only at the outer Run's final text boundary."""

    id = GOAL_CAPABILITY_ID

    def __init__(
        self,
        goal: GoalView | None,
        *,
        changed: Callable[[GoalView], Awaitable[None]] | None = None,
    ) -> None:
        self.goal = goal
        self._changed = changed
        self._active_run_id: str | None = None
        self._completion_candidate = False
        self._input_base = goal.input_tokens if goal is not None else 0
        self._output_base = goal.output_tokens if goal is not None else 0

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Callable[[], Awaitable[Any]]) -> Any:
        if self._active_run_id is not None:
            return await handler()
        self._active_run_id = ctx.run_id
        try:
            if self.goal is not None and self.goal.active:
                if self.goal.status == "suspended":
                    self.goal = self.goal.model_copy(update={"status": "working"})
                await self._publish(ctx)
            result = await handler()
            if self._completion_candidate and self.goal is not None:
                self.goal = self.goal.model_copy(update={"status": "verified"})
                await self._publish(ctx)
            return result
        finally:
            self._active_run_id = None

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if ctx.run_id == self._active_run_id and self.goal is not None:
            # Native steering can drain after a final text candidate. A later
            # request invalidates that candidate without consuming Goal budget.
            if self._completion_candidate:
                self._completion_candidate = False
                self.goal = self.goal.model_copy(update={"status": "working"})
                await self._publish(ctx)
            self._usage(ctx.deps.usage_snapshot.summary, ctx.deps.usage_snapshot.usage_id)
            await self._save(ctx)
        return request_context

    async def on_event(self, ctx: RunContext[AgentContext], *, event: AgentStreamEvent) -> None:
        if (
            ctx.run_id == self._active_run_id
            and isinstance(event, ContextRestoredEvent)
            and self.goal is not None
            and self.goal.active
        ):
            self.goal = self.goal.model_copy(update={"needs_restore_audit": True, "restore_source": event.source})
            await self._publish(ctx)

    async def after_output_process(self, ctx: RunContext[AgentContext], *, output_context: Any, output: Any) -> Any:
        # Plain text bypasses native validate hooks. The process hook is the
        # final output boundary shared with ordinary Agent output validators.
        del output_context
        goal = self.goal
        if (
            ctx.partial_output
            or ctx.run_id != self._active_run_id
            or goal is None
            or not goal.active
            or not isinstance(output, str)
        ):
            return output
        marker = has_completion_marker(output)
        if marker and not goal.needs_restore_audit:
            self._completion_candidate = True
            self.goal = goal.model_copy(update={"status": "checking"})
        else:
            audit = marker and goal.needs_restore_audit
            prompt = (
                post_restore_audit_prompt(goal.objective, goal.restore_source)
                if audit
                else goal_check_prompt(goal.objective)
            )
            if goal.iteration >= goal.max_iterations:
                self.goal = goal.model_copy(update={"status": "max_iterations"})
            else:
                # Enqueue before recording progress: an unavailable native queue
                # must fail execution rather than display a fictitious iteration.
                continuation = TextContent(prompt, metadata={"display": False, "source_id": GOAL_CAPABILITY_ID})
                if ctx.enqueue(ModelRequest(parts=[UserPromptPart([continuation])]), priority="asap") is None:
                    raise RuntimeError("Goal continuation could not be enqueued")
                self.goal = goal.model_copy(
                    update={
                        "iteration": goal.iteration + 1,
                        "status": "auditing" if audit else "checking",
                        "needs_restore_audit": False if audit else goal.needs_restore_audit,
                        "restore_source": None if audit else goal.restore_source,
                    }
                )
        self._usage(ctx.deps.usage_snapshot.summary, ctx.deps.usage_snapshot.usage_id)
        await self._publish(ctx)
        return output

    async def finish(
        self,
        state: HarnessState,
        *,
        status: Literal["completed", "suspended", "cancelled", "failed"],
        usage: RunUsageSummary | None = None,
    ) -> HarnessState:
        """Finalize the portable candidate before the Host publishes its head."""
        if self.goal is None:
            return await to_thread.run_sync(with_goal, state, None)
        snapshot = await to_thread.run_sync(UsageSnapshot.from_state, state)
        if snapshot is not None:
            self._usage(snapshot.summary, snapshot.usage_id)
        elif usage is not None:
            self._usage(usage)
        if self.goal.active or (self.goal.status == "verified" and status in {"failed", "cancelled"}):
            terminal: dict[str, GoalStatus] = {
                "completed": "unverified_stop",
                "suspended": "suspended",
                "cancelled": "cancelled",
                "failed": "error",
            }
            self.goal = self.goal.model_copy(update={"status": terminal[status]})
        if self._changed is not None:
            await self._changed(self.goal)
        return await to_thread.run_sync(with_goal, state, self.goal)

    def _usage(self, usage: RunUsageSummary, usage_id: str | None = None) -> None:
        if self.goal is None:
            return
        input_base, output_base = self._input_base, self._output_base
        if usage_id is not None:
            if self.goal.usage_id == usage_id:
                input_base, output_base = self.goal.usage_input_base, self.goal.usage_output_base
            else:
                input_base, output_base = self.goal.input_tokens, self.goal.output_tokens
        self.goal = self.goal.model_copy(
            update={
                "input_tokens": input_base + usage.input_tokens,
                "output_tokens": output_base + usage.output_tokens,
                "usage_id": usage_id,
                "usage_input_base": input_base,
                "usage_output_base": output_base,
            }
        )

    async def _save(self, ctx: RunContext[AgentContext]) -> None:
        if self.goal is not None:
            await ctx.deps.state.write(GOAL_CAPABILITY_ID, self.goal, version=GOAL_STATE_VERSION)

    async def _publish(self, ctx: RunContext[AgentContext]) -> None:
        await self._save(ctx)
        if self.goal is not None and self._changed is not None:
            await self._changed(self.goal)
