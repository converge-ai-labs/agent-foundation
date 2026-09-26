from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from a13n_harness import (
    DeferredToolResume,
    HarnessBuilder,
    RunBindings,
    RunError,
)
from a13n_harness.capabilities import CompactionCapability, CompactionPolicy
from a13n_harness.tools import (
    APPROVAL_PRESENTATION_KEY,
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from pydantic_ai import DeferredToolResults, Tool, ToolApproved
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio


def _model() -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returns:
            yield {
                0: DeltaToolCall(
                    name="change",
                    json_args=json.dumps({"value": 1}),
                    tool_call_id="approval-1",
                )
            }
        else:
            yield f"done:{returns[-1].content}"

    return FunctionModel(stream_function=stream)


def _metadata(resolver=None) -> HarnessToolMetadata:
    return HarnessToolMetadata(
        tool_id="change",
        effects=frozenset({"write"}),
        credential_audiences=(),
        idempotency="provider_key",
        output_policy=ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096),
        resource_resolver=resolver,
    )


@dataclass
class _Policy:
    decision: InvocationPolicyDecision
    seen_values: list[int]

    async def __call__(self, invocation, metadata, *, context):
        del metadata, context
        self.seen_values.append(invocation.normalized_arguments["value"])
        return self.decision


def _build(
    executed: list[int],
    *,
    resolver=None,
    requires_approval: bool = True,
    retain_inputs: bool = False,
):
    def change(value: int) -> int:
        executed.append(value)
        return value

    capabilities = [
        Capability(
            tools=[
                HarnessTool(
                    change,
                    harness_metadata=_metadata(resolver),
                    requires_approval=requires_approval,
                )
            ],
            id="test-tools",
        )
    ]
    if retain_inputs:
        capabilities.append(CompactionCapability(CompactionPolicy(trigger_tokens=1_000_000)))
    return HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model(),
        capabilities=tuple(capabilities),
    )


async def _suspend(executable, policy: _Policy):
    result = await executable.run(
        "go",
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=policy),)),
    )
    assert result.status == "suspended"
    assert result.state is not None
    assert result.deferred is not None
    assert len(result.deferred.approvals) == 1
    return result


async def test_native_approval_suspends_and_resumes_with_fresh_authority() -> None:
    executed: list[int] = []
    executable = _build(executed)
    first = await _suspend(executable, _Policy(InvocationPolicyDecision.allow(), []))
    requests = first.deferred
    assert requests is not None

    fresh_policy = _Policy(InvocationPolicyDecision.allow(), [])
    second = await executable.run(
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=fresh_policy),)),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(requests, requests.build_results(approve_all=True)),
    )

    assert second.status == "completed"
    assert executed == [1]
    assert fresh_policy.seen_values == [1]
    from a13n_harness.usage import UsageSnapshot

    assert first.state is not None and second.state is not None
    before = UsageSnapshot.from_state(first.state)
    after = UsageSnapshot.from_state(second.state)
    assert before is not None and after is not None
    assert before.usage_id != after.usage_id
    assert first.usage.requests == second.usage.requests == 1
    assert second.usage.tool_calls == 1


async def test_deferred_continuation_restores_the_retained_input_ledger() -> None:
    executed: list[int] = []
    executable = _build(executed, retain_inputs=True)
    first = await _suspend(executable, _Policy(InvocationPolicyDecision.allow(), []))
    assert first.state is not None and first.deferred is not None
    first_retained = first.state.agent_context_state.entries["a13n.steering"].data["retained_requests"]
    assert len(first_retained) == 1

    requests = first.deferred
    second = await executable.run(
        bindings=RunBindings.embedded(
            capabilities=(InvocationPolicyCapability(evaluator=_Policy(InvocationPolicyDecision.allow(), [])),)
        ),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(requests, requests.build_results(approve_all=True)),
    )

    assert second.status == "completed"
    assert second.state is not None
    second_retained = second.state.agent_context_state.entries["a13n.steering"].data["retained_requests"]
    assert second_retained == first_retained
    assert executed == [1]


async def test_policy_requested_approval_is_satisfied_on_native_resume() -> None:
    executed: list[int] = []
    executable = _build(executed, requires_approval=False)
    first = await _suspend(
        executable,
        _Policy(InvocationPolicyDecision.require_approval("confirm write"), []),
    )
    requests = first.deferred
    assert requests is not None
    call_id = requests.approvals[0].tool_call_id
    assert requests.metadata[call_id][APPROVAL_PRESENTATION_KEY] == {
        "tool_id": "change",
        "target": "",
        "reason": "Tool policy requires approval.",
    }

    second = await executable.run(
        bindings=RunBindings.embedded(
            capabilities=(
                InvocationPolicyCapability(
                    evaluator=_Policy(InvocationPolicyDecision.require_approval("confirm write"), []),
                ),
            )
        ),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            requests,
            requests.build_results(
                approve_all=True,
                metadata={call_id: {"approved_by": "user-1"}},
            ),
        ),
    )

    assert second.status == "completed"
    assert executed == [1]


async def test_approved_override_is_schema_validated_and_resources_are_resolved_again() -> None:
    executed: list[int] = []
    resolved: list[int] = []

    async def resolver(arguments, *, context):
        del context
        resolved.append(arguments["value"])
        return ()

    executable = _build(executed, resolver=resolver)
    first = await _suspend(executable, _Policy(InvocationPolicyDecision.allow(), []))
    requests = first.deferred
    assert requests is not None
    call_id = requests.approvals[0].tool_call_id

    second = await executable.run(
        bindings=RunBindings.embedded(
            capabilities=(InvocationPolicyCapability(evaluator=_Policy(InvocationPolicyDecision.allow(), [])),)
        ),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            requests,
            requests.build_results(approvals={call_id: ToolApproved(override_args={"value": "4"})}),
        ),
    )

    assert second.status == "completed"
    assert executed == [4]
    assert resolved == [4]


async def test_unmanaged_native_approval_remains_unmarked_and_uses_native_resume() -> None:
    executed: list[int] = []

    def change(value: int) -> int:
        executed.append(value)
        return value

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model(),
        capabilities=(Capability(tools=[Tool(change, requires_approval=True)], id="test-tools"),),
    )
    first = await executable.run("go", bindings=RunBindings.embedded())
    assert first.status == "suspended"
    assert first.state is not None and first.deferred is not None
    requests = first.deferred
    call_id = requests.approvals[0].tool_call_id
    assert "a13n.harness.managed-tool-id" not in requests.metadata.get(call_id, {})

    second = await executable.run(
        bindings=RunBindings.embedded(),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            requests,
            requests.build_results(approve_all=True),
        ),
    )

    assert second.status == "completed"
    assert executed == [1]


async def test_host_may_replace_approved_tool_without_historical_identity_attestation() -> None:
    managed_executed: list[int] = []
    first_executable = _build(managed_executed)
    first = await _suspend(
        first_executable,
        _Policy(InvocationPolicyDecision.allow(), []),
    )
    assert first.state is not None and first.deferred is not None
    requests = first.deferred
    call_id = requests.approvals[0].tool_call_id
    requests.metadata[call_id] = {"a13n.harness.managed-tool-id": "obsolete-tool"}

    unmanaged_executed: list[int] = []

    def change(value: int) -> int:
        unmanaged_executed.append(value)
        return value

    replacement = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model(),
        capabilities=(Capability(tools=[change], id="test-tools"),),
    )
    result = await replacement.run(
        bindings=RunBindings.embedded(),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(requests, requests.build_results(approve_all=True)),
    )
    assert result.status == "completed"
    assert managed_executed == []
    assert unmanaged_executed == [1]


async def test_live_deny_after_approval_never_dispatches() -> None:
    executed: list[int] = []
    executable = _build(executed)
    first = await _suspend(executable, _Policy(InvocationPolicyDecision.allow(), []))
    requests = first.deferred
    assert requests is not None

    second = await executable.run(
        bindings=RunBindings.embedded(
            capabilities=(InvocationPolicyCapability(evaluator=_Policy(InvocationPolicyDecision.deny("revoked"), [])),)
        ),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(requests, requests.build_results(approve_all=True)),
    )

    assert second.status == "completed"
    assert executed == []


async def test_resume_requires_prior_state_and_exact_category_coverage() -> None:
    executed: list[int] = []
    executable = _build(executed)
    first = await _suspend(executable, _Policy(InvocationPolicyDecision.allow(), []))
    requests = first.deferred
    assert requests is not None

    with pytest.raises(RunError) as no_state:
        executable.stream(
            bindings=RunBindings.embedded(),
            deferred_resume=DeferredToolResume(requests, requests.build_results(approve_all=True)),
        )
    assert no_state.value.code == "deferred_state_required"

    with pytest.raises(RunError) as incomplete:
        executable.stream(
            bindings=RunBindings.embedded(),
            previous_state=first.state,
            deferred_resume=DeferredToolResume(requests, DeferredToolResults()),
        )
    assert incomplete.value.code == "deferred_results_incomplete"

    call_id = requests.approvals[0].tool_call_id
    with pytest.raises(RunError) as wrong_category:
        executable.stream(
            bindings=RunBindings.embedded(),
            previous_state=first.state,
            deferred_resume=DeferredToolResume(requests, DeferredToolResults(calls={call_id: "wrong"})),
        )
    assert wrong_category.value.code == "deferred_results_incomplete"


async def test_resume_rejects_non_native_approval_values_and_non_finite_overrides() -> None:
    executable = _build([])
    first = await _suspend(executable, _Policy(InvocationPolicyDecision.allow(), []))
    assert first.state is not None and first.deferred is not None
    requests = first.deferred
    call_id = requests.approvals[0].tool_call_id

    with pytest.raises(RunError) as invalid_type:
        executable.stream(
            bindings=RunBindings.embedded(),
            previous_state=first.state,
            deferred_resume=DeferredToolResume(
                requests,
                DeferredToolResults(approvals={call_id: "approved"}),
            ),
        )
    assert invalid_type.value.code == "deferred_approval_invalid"

    with pytest.raises(RunError) as non_finite:
        executable.stream(
            bindings=RunBindings.embedded(),
            previous_state=first.state,
            deferred_resume=DeferredToolResume(
                requests,
                DeferredToolResults(approvals={call_id: ToolApproved(override_args={"value": float("nan")})}),
            ),
        )
    assert non_finite.value.code == "deferred_results_invalid"


@pytest.mark.parametrize("replacement", [False, True])
async def test_current_resources_are_resolved_without_historical_attestation(replacement):
    from a13n_harness.tools import CanonicalResource

    executed = []
    connection = "mount-first"
    backing = "env-shared:1"

    async def resources(arguments, *, context):
        return (
            CanonicalResource(
                namespace="environment",
                kind="file",
                identifier=f"{connection}:{backing}:/work",
            ),
        )

    executable = _build(executed, resolver=resources, requires_approval=False)
    policy = _Policy(InvocationPolicyDecision.require_approval(), [])
    suspended = await _suspend(executable, policy)
    connection = "mount-resumed"
    if replacement:
        backing = "env-shared:2"
    # Current policy evaluates newly resolved resources; saved metadata is advisory.
    result = await executable.run(
        previous_state=suspended.state,
        deferred_resume=DeferredToolResume(suspended.deferred, suspended.deferred.build_results(approve_all=True)),
        bindings=RunBindings.embedded(
            capabilities=(InvocationPolicyCapability(evaluator=_Policy(InvocationPolicyDecision.allow(), [])),)
        ),
    )
    assert result.status == "completed"
    assert executed == [1]


# Current deny wins; resource changes and native overrides are not historical proof failures.
@pytest.mark.parametrize(
    ("kind", "change"),
    [
        ("file", "connection"),
        ("mount", "connection"),
        ("file", "path"),
        ("mount", "path"),
        ("file", "arguments"),
        ("mount", "deny"),
    ],
)
async def test_environment_approval_ignores_connection_identity(kind, change):
    from a13n_harness.environment._resources import selection_resource
    from a13n_harness.environment.providers import FileScopeSelection
    from a13n_harness.providers.environment.models import EnvironmentPath

    executed = []
    selection = FileScopeSelection(
        logical_path="/workspace/work",
        resolved_path=EnvironmentPath(mount_id="mount-first", path="/work"),
        observed_generation="generation-first",
    )

    async def resources(arguments, *, context):
        return (selection_resource(selection, kind=kind),)

    executable = _build(executed, resolver=resources, requires_approval=False)
    first = await _suspend(executable, _Policy(InvocationPolicyDecision.require_approval(), []))
    selection = FileScopeSelection(
        logical_path="/workspace/work",
        resolved_path=EnvironmentPath(mount_id="mount-resumed", path="/other" if change == "path" else "/work"),
        observed_generation="generation-resumed",
    )
    requests = first.deferred
    assert requests is not None
    results = requests.build_results(approve_all=True)
    if change == "arguments":
        results = DeferredToolResults(
            approvals={requests.approvals[0].tool_call_id: ToolApproved(override_args={"value": 2})}
        )
    fresh_policy = _Policy(
        InvocationPolicyDecision.deny("current denial") if change == "deny" else InvocationPolicyDecision.allow(), []
    )
    result = await executable.run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(requests, results),
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=fresh_policy),)),
    )
    assert result.status == "completed"
    assert executed == ([] if change == "deny" else [2 if change == "arguments" else 1])


async def test_consumed_approval_does_not_freeze_later_tool_surfaces() -> None:
    executed: list[int] = []

    def change(value: int) -> int:
        executed.append(value)
        return value

    async def prepare(ctx, definition):
        return None if executed else definition

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model(),
        capabilities=(
            Capability(
                tools=[HarnessTool(change, harness_metadata=_metadata(), requires_approval=True, prepare=prepare)],
                id="test-tools",
            ),
        ),
    )
    first = await executable.run("go")
    assert first.status == "suspended" and first.deferred is not None
    second = await executable.run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(first.deferred, first.deferred.build_results(approve_all=True)),
    )
    assert second.output_or_raise() == "done:1"
    assert executed == [1]


async def test_deferred_call_ids_can_be_reused_in_later_responses() -> None:
    from pydantic_ai.messages import UserPromptPart

    executed: list[int] = []

    def change(value: int) -> int:
        executed.append(value)
        return value

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        if isinstance(messages[-1], ModelRequest) and any(
            isinstance(part, UserPromptPart) for part in messages[-1].parts
        ):
            yield {0: DeltaToolCall(name="change", json_args='{"value":1}', tool_call_id="reused-call")}
        else:
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(
            Capability(
                tools=[HarnessTool(change, harness_metadata=_metadata(), requires_approval=True)], id="test-tools"
            ),
        ),
    )
    previous = None
    for prompt in ("first", "again"):
        suspended = await executable.run(prompt, previous_state=previous)
        assert suspended.status == "suspended" and suspended.deferred is not None
        completed = await executable.run(
            previous_state=suspended.state,
            deferred_resume=DeferredToolResume(suspended.deferred, suspended.deferred.build_results(approve_all=True)),
        )
        assert completed.output_or_raise() == "done"
        previous = completed.state
    assert executed == [1, 1]


@pytest.mark.parametrize("retry", [False, True])
async def test_deferred_resume_still_rejects_results_integrated_after_the_pending_response(retry: bool) -> None:
    from a13n_harness import HarnessState
    from pydantic_ai.messages import RetryPromptPart

    executable = _build([])
    suspended = await executable.run("go")
    assert suspended.state is not None and suspended.deferred is not None
    call = suspended.deferred.approvals[0]
    result_part = (RetryPromptPart if retry else ToolReturnPart)(
        content="already handled", tool_name=call.tool_name, tool_call_id=call.tool_call_id
    )
    integrated = HarnessState.new(message_history=[*suspended.state.message_history, ModelRequest(parts=[result_part])])
    with pytest.raises(RunError) as error:
        await executable.run(
            previous_state=integrated,
            deferred_resume=DeferredToolResume(suspended.deferred, suspended.deferred.build_results(approve_all=True)),
        )
    assert error.value.code == "deferred_request_completed"


@pytest.mark.parametrize("decision", [True, False, ToolApproved(override_args={"value": 7})])
@pytest.mark.parametrize("historical_metadata", [False, True])
async def test_host_constructed_history_accepts_native_decisions(decision, historical_metadata):
    from a13n_harness import HarnessState
    from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability
    from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
    from pydantic_ai.tools import DeferredToolRequests

    executed = []
    call = ToolCallPart("change", {"value": 5}, "host-call")
    state = HarnessState.new(
        message_history=[
            ModelRequest(parts=[UserPromptPart("Please change the value")]),
            ModelResponse(parts=[call]),
        ]
    )
    metadata = (
        {
            "host-call": {
                "a13n.harness.tool-approval": {"tool_id": "old", "binding": "old", "requested_sources": []},
                "a13n.resource-approval": {"resources": ["obsolete"]},
                "a13n.harness.managed-tool-id": "old",
            }
        }
        if historical_metadata
        else {}
    )
    requests = DeferredToolRequests(approvals=[call], metadata=metadata)

    def change(value: int) -> int:
        executed.append(value)
        return value

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model(),
        capabilities=(
            Capability(tools=[HarnessTool(change, harness_metadata=_metadata())], id="host-tools"),
            ToolPermissionsCapability(ToolPermissions(default="ask")),
        ),
    )
    policy = _Policy(InvocationPolicyDecision.require_approval(), [])
    result = await executable.run(
        previous_state=state,
        deferred_resume=DeferredToolResume(requests, DeferredToolResults(approvals={"host-call": decision})),
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=policy),)),
    )
    assert result.status == "completed"
    assert executed == ([] if decision is False else [7 if isinstance(decision, ToolApproved) else 5])
    assert policy.seen_values == executed
