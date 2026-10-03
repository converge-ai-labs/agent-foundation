"""Primary-attempt input annotation and source-typed observation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestContext

from a13n_harness.content import annotate_prompt
from a13n_harness.context import AgentContext
from a13n_harness.events import input_events
from a13n_harness.model_context import ModelContextCoordinatorCapability

INPUT_CAPABILITY_ID = "a13n.input"


@dataclass(init=False)
class InputCapability(AbstractCapability[AgentContext]):
    """Keep primary input handling independent of active-run steering."""

    id = INPUT_CAPABILITY_ID

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wraps=(ModelContextCoordinatorCapability,))

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Callable[[], Awaitable[Any]]) -> Any:
        state = ctx.deps._model_input
        attempt_id = state.attempt_id
        if attempt_id is not None and attempt_id == ctx.run_id and not state.observed:
            state.observed = True
            if state.content is not None:
                for event in input_events(state.content, source=state.source, input_id=attempt_id):
                    await ctx.emit(event)
        return await handler()

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        # Bind primary input before context appends its own user-role parts.
        state = ctx.deps._model_input
        if (
            state.attempt_id is not None
            and state.attempt_id == ctx.run_id
            and ctx.run_step == 1
            and state.content is not None
            and not state.annotated
        ):
            request = ctx.messages[-1]
            if isinstance(request, ModelRequest):
                index = next(
                    index
                    for index in range(len(request.parts) - 1, -1, -1)
                    if isinstance(request.parts[index], UserPromptPart)
                )
                annotated = annotate_prompt(request, index, state.content)
                ctx.messages[-1] = annotated
                request_context.messages[-1] = annotated
                state.annotated = True
        return request_context
