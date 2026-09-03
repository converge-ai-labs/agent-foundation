from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from a13n_harness import (
    AgentContext,
    AgentDefinition,
    AgentSpec,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.errors import DefinitionError
from a13n_service.interactions.harness_control import (
    FOUNDATION_RUN_CONTROL_CAPABILITY_ID,
    FoundationRunControlCapability,
    compose_run_control,
)
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.models.function import AgentInfo, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


@dataclass
class _RecordingCoordinator:
    trace: list[str] = field(default_factory=list)
    calls: list[tuple[str, AgentContext]] = field(default_factory=list)

    async def bind_model_attempt(self, ctx: RunContext[AgentContext]) -> None:
        self.calls.append(("bind", ctx.deps))

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> None:
        del request_context
        self.trace.append("control_before_model")
        self.calls.append(("before_model", ctx.deps))

    async def after_model_response(
        self,
        ctx: RunContext[AgentContext],
        response: ModelResponse,
    ) -> None:
        del response
        self.trace.append("control_after_model")
        self.calls.append(("after_model", ctx.deps))

    async def after_tool_batch(self, ctx: RunContext[AgentContext], result: Any) -> None:
        del result
        self.calls.append(("after_tools", ctx.deps))


@dataclass
class _OuterRecordingCapability(AbstractCapability[AgentContext]):
    trace: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    id: str | None = "test.outer"

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="outermost")

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        del ctx
        self.trace.append("ordinary_before_model")
        self.calls.append("before_model")
        return request_context

    async def after_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        del ctx, request_context
        self.trace.append("ordinary_after_model")
        self.calls.append("after_model")
        return response


async def _stream_model(
    messages: list[ModelMessage],
    info: AgentInfo,
) -> AsyncIterator[str | DeltaToolCalls]:
    del messages, info
    yield "done"


def _definition(*capabilities: AbstractCapability[AgentContext]) -> AgentDefinition[str]:
    return AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=_stream_model),
        capabilities=capabilities,
    )


async def test_control_capability_is_outermost_and_preserves_harness_values() -> None:
    trace: list[str] = []
    coordinator = _RecordingCoordinator(trace=trace)
    ordinary_outer = _OuterRecordingCapability(trace=trace)
    definition = compose_run_control(_definition(ordinary_outer), coordinator)

    control = definition.capabilities[0]
    assert isinstance(control, FoundationRunControlCapability)
    assert control.id == FOUNDATION_RUN_CONTROL_CAPABILITY_ID
    assert control.get_ordering().position == "outermost"

    result = (
        await HarnessBuilder(instrumentation=None)
        .build(definition)
        .run(
            "hello",
            bindings=RunBindings.embedded(),
        )
    )

    assert result.output_or_raise() == "done"
    names = [name for name, _ in coordinator.calls]
    assert names == ["bind", "before_model", "after_model", "after_tools"]
    assert ordinary_outer.calls == ["before_model", "after_model"]
    assert trace == [
        "control_before_model",
        "ordinary_before_model",
        "ordinary_after_model",
        "control_after_model",
    ]
    contexts = [context for _, context in coordinator.calls]
    assert all(context is contexts[0] for context in contexts)


async def test_control_capability_binds_fresh_logical_run_contexts() -> None:
    coordinator = _RecordingCoordinator()
    executable = HarnessBuilder(instrumentation=None).build(compose_run_control(_definition(), coordinator))

    await executable.run("first", bindings=RunBindings.embedded())
    await executable.run("second", bindings=RunBindings.embedded())

    contexts = [context for name, context in coordinator.calls if name == "bind"]
    assert len(contexts) == 2
    assert contexts[0] is not contexts[1]


async def test_harness_rejects_a_duplicate_reserved_control_id() -> None:
    coordinator = _RecordingCoordinator()
    duplicate = _OuterRecordingCapability(id=FOUNDATION_RUN_CONTROL_CAPABILITY_ID)

    with pytest.raises(DefinitionError) as error:
        HarnessBuilder(instrumentation=None).build(compose_run_control(_definition(duplicate), coordinator))

    assert error.value.code == "agent_build_failed"
