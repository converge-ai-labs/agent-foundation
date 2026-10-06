"""Run-local native capture before external stream consumption."""

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import AgentStreamEvent

from a13n_harness.capabilities.input import InputCapability
from a13n_harness.context import AgentContext
from a13n_harness.events import _RunEventEmitter


class ProducerCaptureCapability(AbstractCapability[AgentContext]):
    """Observe one native attempt, excluding reentrant helper-model execution."""

    id = "a13n.producer-capture"

    def __init__(self, emitter: _RunEventEmitter, attempt_id: str) -> None:
        self.emitter, self.attempt_id = emitter, attempt_id

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wraps=(InputCapability,))

    async def on_event(self, ctx: RunContext[AgentContext], *, event: AgentStreamEvent) -> None:
        if ctx.run_id == self.attempt_id:
            self.emitter.observe(event)
