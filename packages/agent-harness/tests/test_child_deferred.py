from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from a13n_harness import (
    AgentContext,
    AgentIdentityRef,
    AgentInstanceContext,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.environment.advanced import NoopEnvironmentRunBinding
from a13n_harness.tools import (
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability, HandleDeferredToolCalls
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolDenied

pytestmark = pytest.mark.anyio

_DENIAL = "Deferred tool interaction is unavailable in subagent runs."
_DENIAL_RESULT = repr(ToolDenied(_DENIAL))


def _child_bindings(*, capabilities: tuple[Any, ...] = ()) -> RunBindings:
    return RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="child"),
            agent_instance_id="child-1",
            parent_agent_instance_id="parent-1",
            delegation_id="delegation-1",
        ),
        environment=NoopEnvironmentRunBinding(),
        capabilities=capabilities,
    )


def _tool_returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _single_tool_model(tool_name: str) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = _tool_returns(messages)
        if not returns:
            yield {
                0: DeltaToolCall(
                    name=tool_name,
                    json_args=json.dumps({"value": 7}),
                    tool_call_id="deferred-1",
                )
            }
            return
        yield str(returns[-1].content)

    return FunctionModel(stream_function=stream)


@pytest.mark.parametrize("deferred_error", [CallDeferred, ApprovalRequired])
async def test_child_denies_dynamic_function_deferral_and_continues_same_run(
    deferred_error: type[CallDeferred] | type[ApprovalRequired],
) -> None:
    calls = 0

    def dynamic_action(value: int) -> int:
        nonlocal calls
        calls += 1
        raise deferred_error({"value": value})

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_single_tool_model("dynamic_action"),
        capabilities=(Capability(tools=[dynamic_action], id="dynamic-tools"),),
    )

    result = await executable.run("go", bindings=_child_bindings())

    assert result.status == "completed"
    assert _DENIAL in result.output_or_raise()
    assert result.usage.requests == 2
    assert calls == 1


async def test_child_denies_managed_policy_approval_and_continues_same_run() -> None:
    executed = False

    def managed_action(value: int) -> int:
        nonlocal executed
        executed = True
        return value

    metadata = HarnessToolMetadata(
        tool_id="managed.action",
        effects=frozenset({"write"}),
        credential_audiences=(),
        idempotency="none",
        output_policy=ToolOutputPolicy(max_inline_bytes=512, max_output_bytes=1024),
    )

    async def require_approval(*args: Any, **kwargs: Any) -> InvocationPolicyDecision:
        del args, kwargs
        return InvocationPolicyDecision.require_approval("review required")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_single_tool_model("managed_action"),
        capabilities=(
            Capability(
                tools=[HarnessTool(managed_action, harness_metadata=metadata)],
                id="managed-tools",
            ),
        ),
    )

    result = await executable.run(
        "go",
        bindings=_child_bindings(
            capabilities=(InvocationPolicyCapability(evaluator=require_approval),),
        ),
    )

    assert result.status == "completed"
    assert _DENIAL in result.output_or_raise()
    assert result.usage.requests == 2
    assert executed is False


async def test_mandatory_child_denial_precedes_user_deferred_handler_while_root_handler_still_runs() -> None:
    handler_calls: list[DeferredToolRequests] = []

    def dynamic_action(value: int) -> int:
        raise CallDeferred({"value": value})

    async def handle_deferred(
        ctx: RunContext[AgentContext],
        requests: DeferredToolRequests,
    ) -> DeferredToolResults:
        del ctx
        handler_calls.append(requests)
        return DeferredToolResults(
            calls={request.tool_call_id: "handled by host" for request in requests.calls},
        )

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_single_tool_model("dynamic_action"),
        capabilities=(
            Capability(tools=[dynamic_action], id="dynamic-tools"),
            HandleDeferredToolCalls(handler=handle_deferred, id="user-deferred-handler"),
        ),
    )

    child_result = await executable.run("go", bindings=_child_bindings())

    assert child_result.output_or_raise() == _DENIAL_RESULT
    assert handler_calls == []

    root_result = await executable.run("go", bindings=RunBindings.embedded())

    assert len(handler_calls) == 1
    assert root_result.output_or_raise() == "handled by host"


async def test_child_completes_mixed_ordinary_and_dynamic_deferred_batch() -> None:
    ordinary_calls: list[int] = []

    def ordinary_action(value: int) -> int:
        ordinary_calls.append(value)
        return value + 1

    def dynamic_action(value: int) -> int:
        raise CallDeferred({"value": value})

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = _tool_returns(messages)
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="ordinary_action",
                    json_args=json.dumps({"value": 2}),
                    tool_call_id="ordinary-1",
                ),
                1: DeltaToolCall(
                    name="dynamic_action",
                    json_args=json.dumps({"value": 3}),
                    tool_call_id="deferred-1",
                ),
            }
            return
        by_id = {part.tool_call_id: str(part.content) for part in returns}
        yield json.dumps(by_id, sort_keys=True)

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[ordinary_action, dynamic_action], id="mixed-tools"),),
    )

    result = await executable.run("go", bindings=_child_bindings())

    assert result.status == "completed"
    assert ordinary_calls == [2]
    assert result.output_or_raise() == json.dumps(
        {"deferred-1": _DENIAL_RESULT, "ordinary-1": "3"},
        sort_keys=True,
    )
