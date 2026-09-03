"""Mandatory Foundation control hooks at public Harness lifecycle boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from a13n_harness import AgentContext, AgentDefinition
from a13n_harness.errors import DefinitionError
from pydantic_ai import CallToolsNode, RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    AgentNode,
    CapabilityOrdering,
    NodeResult,
)
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext

FOUNDATION_RUN_CONTROL_CAPABILITY_ID = "a13n.foundation.run-control"


class RunControlCoordinator(Protocol):
    """Attempt-scoped Foundation operations awaited by the control Capability."""

    async def bind_model_attempt(self, ctx: RunContext[AgentContext]) -> None: ...

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> None: ...

    async def after_model_response(
        self,
        ctx: RunContext[AgentContext],
        response: ModelResponse,
    ) -> None: ...

    async def after_tool_batch(
        self,
        ctx: RunContext[AgentContext],
        result: NodeResult[AgentContext],
    ) -> None: ...


@dataclass(init=False)
class FoundationRunControlCapability(AbstractCapability[AgentContext]):
    """Direct, model-inert rendezvous with the current Foundation RunAttempt."""

    id = FOUNDATION_RUN_CONTROL_CAPABILITY_ID

    def __init__(self, coordinator: RunControlCoordinator) -> None:
        self._coordinator = coordinator

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost")

    async def for_run(
        self,
        ctx: RunContext[AgentContext],
    ) -> AbstractCapability[AgentContext]:
        await self._coordinator.bind_model_attempt(ctx)
        return _ActiveRunControlCapability(self._coordinator, ctx.deps)


@dataclass(init=False)
class _ActiveRunControlCapability(FoundationRunControlCapability):
    """One ModelAttempt binding that cannot be reused by another logical Run."""

    def __init__(self, coordinator: RunControlCoordinator, context: AgentContext) -> None:
        super().__init__(coordinator)
        self._context = context

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        self._require_context(ctx)
        await self._coordinator.before_model_request(ctx, request_context)
        return request_context

    async def after_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        del request_context
        self._require_context(ctx)
        await self._coordinator.after_model_response(ctx, response)
        return response

    async def after_node_run(
        self,
        ctx: RunContext[AgentContext],
        *,
        node: AgentNode[AgentContext],
        result: NodeResult[AgentContext],
    ) -> NodeResult[AgentContext]:
        self._require_context(ctx)
        if isinstance(node, CallToolsNode):
            await self._coordinator.after_tool_batch(ctx, result)
        return result

    def _require_context(self, ctx: RunContext[AgentContext]) -> None:
        if ctx.deps is not self._context:
            raise DefinitionError(
                "Foundation run control cannot cross logical Harness Runs.",
                code="capability_scope_invalid",
            )


def compose_run_control[OutputT](
    definition: AgentDefinition[OutputT],
    coordinator: RunControlCoordinator,
) -> AgentDefinition[OutputT]:
    """Prepend the mandatory control Capability to one reconstructed root definition."""

    return definition.with_updates(
        capabilities=(
            FoundationRunControlCapability(coordinator),
            *definition.capabilities,
        )
    )


__all__ = [
    "FOUNDATION_RUN_CONTROL_CAPABILITY_ID",
    "FoundationRunControlCapability",
    "RunControlCoordinator",
    "compose_run_control",
]
