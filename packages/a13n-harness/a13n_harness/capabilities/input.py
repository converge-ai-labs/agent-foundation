"""Primary-attempt input annotation before model-context projection."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestContext

from a13n_harness.content import annotate_prompt
from a13n_harness.context import AgentContext
from a13n_harness.model_context import ModelContextCoordinatorCapability

INPUT_CAPABILITY_ID = "a13n.input"


@dataclass(init=False)
class InputCapability(AbstractCapability[AgentContext]):
    """Keep primary input handling independent of active-run steering."""

    id = INPUT_CAPABILITY_ID

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wraps=(ModelContextCoordinatorCapability,))

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
