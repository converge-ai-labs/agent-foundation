from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import pytest
from a13n_harness import (
    AgentContext,
    AgentDefinition,
    AgentIdentityRef,
    AgentInstanceContext,
    DeferredToolResume,
    HarnessBuilder,
    HarnessState,
    RunBindings,
    SubagentDefinition,
)
from a13n_harness.environment.advanced import EmptyEnvironmentRuntime
from a13n_harness.tools import (
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from pydantic import BaseModel
from pydantic_ai import RunContext
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability, HandleDeferredToolCalls
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolDenied

pytestmark = pytest.mark.anyio

_DENIAL = "Deferred tool interaction is unavailable for this Run."
_DENIAL_RESULT = repr(ToolDenied(_DENIAL))


def _child_bindings(*, capabilities: tuple[Any, ...] = ()) -> RunBindings:
    return RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="child"),
            agent_instance_id="child-1",
            parent_agent_instance_id="parent-1",
            delegation_id="delegation-1",
        ),
        environment=EmptyEnvironmentRuntime(),
        deferred_tools_supported=False,
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


# Child and root runs deny through the same unsupported-deferral path; the pairs cover each value once.
@pytest.mark.parametrize(("is_child", "deferred_error"), [(False, CallDeferred), (True, ApprovalRequired)])
async def test_unsupported_run_denies_dynamic_function_deferral_and_continues_same_run(
    deferred_error: type[CallDeferred] | type[ApprovalRequired],
    is_child: bool,
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

    bindings = _child_bindings() if is_child else RunBindings.embedded(deferred_tools_supported=False)
    result = await executable.run("go", bindings=bindings)

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


@pytest.mark.parametrize(("is_child", "approval"), [(True, True), (False, False)])
async def test_host_managed_typed_output_suspends_and_resumes(is_child: bool, approval: bool) -> None:
    class BusinessOutput(BaseModel):
        answer: str

    effects: list[int] = []

    def action(ctx: RunContext[AgentContext], value: int) -> str:
        if approval:
            if not ctx.tool_call_approved:
                raise ApprovalRequired({"value": value})
            effects.append(value)
            return "approved by host"
        raise CallDeferred({"value": value})

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        returns = _tool_returns(messages)
        if not returns:
            yield {0: DeltaToolCall(name="action", json_args='{"value":7}', tool_call_id="action-1")}
        else:
            assert info.output_tools
            yield {
                0: DeltaToolCall(
                    name=info.output_tools[0].name,
                    json_args=json.dumps({"answer": returns[-1].content}),
                    tool_call_id="output-1",
                )
            }

    # Only the business type is authored, including on recursively built children.
    child = AgentDefinition(
        agent=AgentSpec(),
        output_type=BusinessOutput,
        model=FunctionModel(stream_function=stream),
        capabilities=(Capability(tools=[action], id="actions"),),
    )

    async def unused_parent(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        raise AssertionError("Host-managed child resume must not execute the parent model")
        yield "unreachable"

    root = HarnessBuilder().build(
        AgentDefinition(
            agent=AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=unused_parent),
            subagents=(SubagentDefinition(name="worker", description="Work", agent=child),),
        )
    )
    executable = root.subagents.require("worker").executable if is_child else HarnessBuilder().build(child)
    bindings = replace(_child_bindings(), deferred_tools_supported=True) if is_child else RunBindings.embedded()
    first = await executable.run("go", bindings=bindings)

    assert first.status == "suspended"
    assert first.output is None
    assert first.state is not None and first.deferred is not None
    assert effects == []
    requests = first.deferred
    assert requests.metadata["action-1"]["value"] == 7
    state = HarnessState.model_validate_json(first.state.model_dump_json())
    feedback = (
        DeferredToolResults(approvals={"action-1": True})
        if approval
        else DeferredToolResults(calls={"action-1": "executed by host"})
    )
    # The Host resumes the child directly, without executing the parent model.
    second = await executable.run(
        bindings=replace(bindings, environment=EmptyEnvironmentRuntime()),
        previous_state=state,
        deferred_resume=DeferredToolResume(requests, feedback),
    )

    assert second.status == "completed"
    assert second.thread_id == first.thread_id
    assert second.run_id != first.run_id
    assert second.deferred is None
    assert second.output_or_raise() == BusinessOutput(answer="approved by host" if approval else "executed by host")
    assert effects == ([7] if approval else [])
