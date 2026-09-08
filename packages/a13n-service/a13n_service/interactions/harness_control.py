"""Mandatory Service control hooks at public Harness lifecycle boundaries."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal, Protocol

from a13n_harness import AgentContext, AgentDefinition, HarnessState, RunInputValue
from a13n_harness.errors import DefinitionError
from pydantic_ai import Agent, CallToolsNode, RunContext
from pydantic_ai.agent import AbstractAgent, ModelRequestNode
from pydantic_ai.capabilities import (
    AbstractCapability,
    AgentNode,
    CapabilityOrdering,
    Instrumentation,
    NodeResult,
)
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestContext

from .attempts import AttemptContext, AttemptPreparationAccepted
from .objects import StoredRunState

RUN_CONTROL_CAPABILITY_ID = "a13n.service.run-control"


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
    """Callback-local access to the two Harness context operations Service needs."""

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

    async def steer(self, input: RunInputValue) -> str | None: ...

    async def cancel(self) -> None: ...

    async def export_state(self) -> HarnessState: ...


class RunControlPort(Protocol):
    """Attempt-scoped Service operations awaited by the driver and Capability."""

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

    async def before_model_node(self, boundary: HarnessHookBoundary) -> None: ...

    async def before_nested_model_request(self) -> None: ...

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
class RunControlCapability(AbstractCapability[AgentContext]):
    """Direct, model-inert rendezvous with the current Service RunAttempt."""

    id = RUN_CONTROL_CAPABILITY_ID

    def __init__(
        self,
        control: RunControlPort,
        driver: HarnessControlDriver,
        *,
        binding: HarnessContextBinding | None = None,
        running: ContextVar[bool] | None = None,
        nested: bool = False,
    ) -> None:
        self._control = control
        self._driver = driver
        self._binding = binding
        self._running = (
            running if running is not None else ContextVar("a13n_service_model_attempt_active", default=False)
        )
        self._nested = nested

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost", wrapped_by=tuple(_native_anonymous_capabilities()))

    def for_agent(self, agent: AbstractAgent[AgentContext, object]) -> AbstractCapability[AgentContext]:
        # Pydantic has already sorted the complete construction tree, including plugins.
        leaves: list[AbstractCapability[AgentContext]] = []
        agent.root_capability.apply(leaves.append)
        validate_control_order(leaves)
        return self

    async def for_run(
        self,
        ctx: RunContext[AgentContext],
    ) -> AbstractCapability[AgentContext]:
        # Same-Agent compaction nests a native run inside the active attempt.
        # Its temporary summary prompt must never become a Service checkpoint.
        if self._running.get():
            return RunControlCapability(self._control, self._driver, running=self._running, nested=True)
        binding = self._driver.bind_model_attempt(ctx)
        await self._control.bind_model_attempt(binding)
        return RunControlCapability(
            self._control,
            self._driver,
            binding=binding,
            running=self._running,
        )

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Callable[[], Awaitable[Any]]) -> Any:
        token = self._running.set(True)
        try:
            return await handler()
        finally:
            self._running.reset(token)

    async def before_node_run(
        self, ctx: RunContext[AgentContext], *, node: AgentNode[AgentContext]
    ) -> AgentNode[AgentContext]:
        # Offer before the native drain, then confirm from the drained history in
        # before_model_request. This also includes accepted inbox on the first request.
        if not self._nested and isinstance(node, ModelRequestNode):
            async with self._driver.hook_boundary(ctx, self._binding) as boundary:
                await self._control.before_model_node(boundary)
        return node

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        if self._nested:
            await self._control.before_nested_model_request()
            return request_context
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
        if self._nested:
            return response
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
        if not self._nested and isinstance(node, CallToolsNode):
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
            RunControlCapability(control, driver),
            *definition.capabilities,
        )
    )


def validate_control_order(capabilities: Sequence[AbstractCapability[AgentContext]]) -> None:
    """Reject a control replacement or a feature wrapper outside Service hooks."""

    controls = [cap for cap in capabilities if cap.id == RUN_CONTROL_CAPABILITY_ID]
    if len(controls) != 1 or type(controls[0]) is not RunControlCapability:
        raise DefinitionError(
            "Service control Capability identity is invalid.", code="service_control_identity_mismatch"
        )
    # Harness validates the exact types and provenance behind these reserved IDs.
    infrastructure = {
        "a13n.tool-execution-boundary",
        "a13n.lifecycle-events",
        "a13n.steering",
        "a13n.model-context-coordinator",
    }
    hooks = ("before_node_run", "before_model_request", "after_model_request", "after_node_run")
    for cap in capabilities:
        if cap is controls[0]:
            return
        # Pydantic inserts its native enqueue drain before user hooks; it must
        # finish before Service checkpoints the complete request messages.
        if type(cap) in _native_anonymous_capabilities():
            continue
        if cap.id in infrastructure or isinstance(cap, Instrumentation):
            continue
        if any(getattr(type(cap), hook) is not getattr(AbstractCapability, hook) for hook in hooks):
            raise DefinitionError(
                "A Capability wraps the mandatory Service control hooks.",
                code="service_control_order_invalid",
            )


@cache
def _native_anonymous_capabilities() -> frozenset[type[AbstractCapability[AgentContext]]]:
    """Discover auto-injected native hooks through the public baseline Agent tree.

    Anonymous infrastructure has no stable public class export. Use exact types
    from a bare Agent rather than importing private queue implementations or
    admitting arbitrary anonymous feature Capabilities.
    """
    types: set[type[AbstractCapability[AgentContext]]] = set()

    def collect(capability: AbstractCapability[AgentContext]) -> None:
        if capability.id is None:
            types.add(type(capability))

    Agent(deps_type=AgentContext).root_capability.apply(collect)
    return frozenset(types)


__all__ = [
    "RUN_CONTROL_CAPABILITY_ID",
    "HarnessContextBinding",
    "HarnessControlDriver",
    "HarnessHookBoundary",
    "HarnessRunIdentity",
    "RunControlCapability",
    "RunControlPort",
    "compose_run_control",
]
