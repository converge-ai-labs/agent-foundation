"""Mandatory Foundation control hooks at public Harness lifecycle boundaries."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, Protocol

from a13n_harness import AgentContext, AgentDefinition, HarnessState, RunInputValue
from pydantic_ai import CallToolsNode, RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    AgentNode,
    CapabilityOrdering,
    NodeResult,
)
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestContext

from .attempts import AttemptContext, AttemptPreparationAccepted
from .objects import StoredRunState

FOUNDATION_RUN_CONTROL_CAPABILITY_ID = "a13n.foundation.run-control"


@dataclass(frozen=True, slots=True)
class HarnessContextBinding:
    """Opaque driver-issued identity for one internal Harness ModelAttempt."""

    _driver_token: object
    _run_token: object
    _model_attempt_token: object


@dataclass(frozen=True, slots=True)
class HarnessRunIdentity:
    """Bounded identity exposed by the driver after entering one Harness stream."""

    thread_id: str
    run_id: str


class HarnessHookBoundary(Protocol):
    """Callback-local access to the two Harness context operations Foundation needs."""

    async def enqueue(
        self,
        input: RunInputValue,
        *,
        priority: Literal["asap"],
    ) -> str: ...

    async def export_state(
        self,
        complete_messages: Sequence[ModelMessage],
    ) -> HarnessState: ...


class HarnessControlDriver(Protocol):
    """Narrow driver surface available to the Capability and run-control facade."""

    def bind_model_attempt(self, ctx: RunContext[AgentContext]) -> HarnessContextBinding: ...

    def hook_boundary(
        self,
        ctx: RunContext[AgentContext],
        binding: HarnessContextBinding | None,
    ) -> AbstractAsyncContextManager[HarnessHookBoundary]: ...

    def validate_binding(self, binding: HarnessContextBinding) -> None: ...

    def validate_boundary(self, boundary: HarnessHookBoundary) -> None: ...

    async def steer(self, input: RunInputValue) -> str: ...

    async def cancel(self) -> None: ...

    async def export_state(self) -> HarnessState: ...


class RunControlPort(Protocol):
    """Attempt-scoped Foundation operations awaited by the driver and Capability."""

    @property
    def current_context(self) -> AttemptContext: ...

    @property
    def current_state(self) -> StoredRunState: ...

    @property
    def terminal_observation_allowed(self) -> bool:
        """Whether the current Harness terminal item is an ordinary outcome."""
        ...

    async def enter_harness(
        self,
        identity: HarnessRunIdentity,
        preparation: AttemptPreparationAccepted,
    ) -> None: ...

    async def after_stream_entry(self) -> None: ...

    async def bind_model_attempt(self, binding: HarnessContextBinding) -> None: ...

    async def before_model_request(
        self,
        boundary: HarnessHookBoundary,
        request_context: ModelRequestContext,
    ) -> None: ...

    async def after_model_response(
        self,
        boundary: HarnessHookBoundary,
        response: ModelResponse,
    ) -> None: ...

    async def after_tool_batch(
        self,
        boundary: HarnessHookBoundary,
        result: NodeResult[AgentContext],
        complete_messages: Sequence[ModelMessage],
    ) -> None: ...


@dataclass(init=False)
class FoundationRunControlCapability(AbstractCapability[AgentContext]):
    """Direct, model-inert rendezvous with the current Foundation RunAttempt."""

    id = FOUNDATION_RUN_CONTROL_CAPABILITY_ID

    def __init__(
        self,
        control: RunControlPort,
        driver: HarnessControlDriver,
        *,
        binding: HarnessContextBinding | None = None,
    ) -> None:
        self._control = control
        self._driver = driver
        self._binding = binding

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost")

    async def for_run(
        self,
        ctx: RunContext[AgentContext],
    ) -> AbstractCapability[AgentContext]:
        binding = self._driver.bind_model_attempt(ctx)
        await self._control.bind_model_attempt(binding)
        return FoundationRunControlCapability(
            self._control,
            self._driver,
            binding=binding,
        )

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        async with self._driver.hook_boundary(ctx, self._binding) as boundary:
            await self._control.before_model_request(boundary, request_context)
        return request_context

    async def after_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        del request_context
        async with self._driver.hook_boundary(ctx, self._binding) as boundary:
            await self._control.after_model_response(boundary, response)
        return response

    async def after_node_run(
        self,
        ctx: RunContext[AgentContext],
        *,
        node: AgentNode[AgentContext],
        result: NodeResult[AgentContext],
    ) -> NodeResult[AgentContext]:
        if isinstance(node, CallToolsNode):
            async with self._driver.hook_boundary(ctx, self._binding) as boundary:
                await self._control.after_tool_batch(boundary, result, ctx.messages)
        return result


def compose_run_control[OutputT](
    definition: AgentDefinition[OutputT],
    control: RunControlPort,
    driver: HarnessControlDriver,
) -> AgentDefinition[OutputT]:
    """Prepend the mandatory control Capability to one reconstructed root definition."""

    return definition.with_updates(
        capabilities=(
            FoundationRunControlCapability(control, driver),
            *definition.capabilities,
        )
    )


__all__ = [
    "FOUNDATION_RUN_CONTROL_CAPABILITY_ID",
    "FoundationRunControlCapability",
    "HarnessContextBinding",
    "HarnessControlDriver",
    "HarnessHookBoundary",
    "HarnessRunIdentity",
    "RunControlPort",
    "compose_run_control",
]
