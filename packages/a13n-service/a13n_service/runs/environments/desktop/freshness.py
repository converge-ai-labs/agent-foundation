"""Per-Agent observation provenance pinned to the model request that produced a call."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from a13n_harness import AgentContext
from a13n_harness.environment.computer import ComputerCall, ComputerObservation
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError
from pydantic_ai import BinaryContent, RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, ValidatedToolArgs, WrapToolExecuteHandler
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition

READ_ACTIONS = frozenset(
    {
        EnvironmentAction.COMPUTER_DESCRIBE,
        EnvironmentAction.COMPUTER_OBSERVE,
        EnvironmentAction.COMPUTER_ZOOM,
        EnvironmentAction.COMPUTER_READ_CLIPBOARD,
    }
)


@dataclass(frozen=True)
class Observation:
    control_id: str
    generation: int
    episode: int
    reference: ComputerObservation


@dataclass(frozen=True)
class Invocation:
    agent_instance_id: str
    observations: tuple[Observation, ...]
    owner: "Freshness"

    def require(self, control_id: str, generation: int, episode: int, call: ComputerCall) -> None:
        if call.action in READ_ACTIONS or episode == 0:
            return
        matches = tuple(
            item
            for item in self.observations
            if (item.control_id, item.generation, item.episode) == (control_id, generation, episode)
        )
        if not matches or (
            call.observation is not None and not any(item.reference == call.observation for item in matches)
        ):
            raise EnvironmentError(
                "Observe the desktop after handback, then make a new model decision before sending input. "
                "This call did not execute; old observations and already-running scripts cannot authorize input.",
                code="environment_denied",
                details={
                    "dispatch_stage": "pre_dispatch",
                    "reason": "desktop_observation_required",
                    "hint": "Observe the desktop after handback, then make a new model decision before sending input. "
                    "This call did not execute; old observations and already-running scripts cannot authorize input.",
                },
            )

    def observed(self, observation: Observation, image_identifier: str) -> None:
        # Completion only registers a candidate. Permission requires its image in a later request.
        self.owner.observations.setdefault(self.agent_instance_id, {})[image_identifier] = observation


current: ContextVar[Invocation | None] = ContextVar("desktop_invocation", default=None)


@dataclass
class Freshness(AbstractCapability[AgentContext]):
    """Installed on every Agent node; recovery intentionally requires a new observation."""

    id = "service.desktop_freshness"
    observations: dict[str, dict[str, Observation]] = field(default_factory=dict)
    calls: dict[tuple[str, str], Invocation] = field(default_factory=dict)

    def get_ordering(self) -> CapabilityOrdering:
        # Owned execution tasks must capture provenance before their admission creates a task.
        return CapabilityOrdering(position="outermost")

    def invocation(self, instance: str, messages: list[ModelMessage]) -> Invocation:
        candidates = self.observations.get(instance, {})
        visible: dict[str, Observation] = {}
        for message in messages:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if not isinstance(part, (ToolReturnPart, UserPromptPart)):
                    continue
                content = part.content
                items = content if isinstance(content, (list, tuple)) else [content]
                for item in items:
                    if isinstance(item, BinaryContent) and item.is_image and item.identifier in candidates:
                        # Exact whole-image identity only: a crop or surviving split segment is insufficient.
                        visible[item.identifier] = candidates[item.identifier]
        return Invocation(instance, tuple(visible.values()), self)

    async def after_model_request(
        self, ctx: RunContext[AgentContext], *, request_context: ModelRequestContext, response: ModelResponse
    ) -> ModelResponse:
        instance = ctx.deps.instance.agent_instance_id
        # This is the finalized request after compaction and image-input policy, not canonical history.
        source = self.invocation(instance, request_context.messages)
        for part in response.parts:
            if isinstance(part, ToolCallPart):
                self.calls[(instance, part.tool_call_id)] = source
        return response

    async def wrap_tool_execute(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        instance = ctx.deps.instance.agent_instance_id
        source = current.get()
        if source is None or source.agent_instance_id != instance:
            # A recovered/deferred call without its original request never inherits the latest permission.
            source = self.calls.get((instance, call.tool_call_id), Invocation(instance, (), self))
        token = current.set(source)
        try:
            return await handler(args)
        finally:
            current.reset(token)
