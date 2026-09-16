from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from a13n_harness import (
    DeferredToolResume,
    DefinitionError,
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
    verified: list[dict[str, object]] = []

    @dataclass
    class Verifier:
        async def verify(self, invocation, approval_metadata, *, context):
            del invocation, context
            verified.append(dict(approval_metadata))
            return True

    executable = _build(executed, requires_approval=False)
    first = await _suspend(
        executable,
        _Policy(InvocationPolicyDecision.require_approval("confirm write"), []),
    )
    requests = first.deferred
    assert requests is not None
    call_id = requests.approvals[0].tool_call_id
    assert requests.metadata[call_id][APPROVAL_PRESENTATION_KEY] == {
        "target": "",
        "reason": "Tool policy requires approval.",
    }

    second = await executable.run(
        bindings=RunBindings.embedded(
            capabilities=(
                InvocationPolicyCapability(
                    evaluator=_Policy(InvocationPolicyDecision.require_approval("confirm write"), []),
                    approval_verifier=Verifier(),
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
    assert verified == [{"approved_by": "user-1"}]


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


async def test_managed_approval_cannot_remount_to_a_same_named_unmanaged_tool() -> None:
    managed_executed: list[int] = []
    first_executable = _build(managed_executed)
    first = await _suspend(
        first_executable,
        _Policy(InvocationPolicyDecision.allow(), []),
    )
    assert first.state is not None and first.deferred is not None
    requests = first.deferred
    call_id = requests.approvals[0].tool_call_id
    assert requests.metadata[call_id]["a13n.harness.managed-tool-id"] == "change"

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
    denied = _Policy(InvocationPolicyDecision.deny("revoked"), [])
    with pytest.raises(DefinitionError) as exc_info:
        await replacement.run(
            bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=denied),)),
            previous_state=first.state,
            deferred_resume=DeferredToolResume(
                requests,
                requests.build_results(approve_all=True),
            ),
        )

    assert exc_info.value.code == "deferred_surface_mismatch"
    assert managed_executed == []
    assert unmanaged_executed == []
    assert denied.seen_values == []


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
async def test_custom_resource_revision_can_bind_backing_across_run_connections(replacement):
    from a13n_harness.tools import CanonicalResource

    executed = []
    connection = "mount-first"
    backing = "env-shared:1"

    async def resources(arguments, *, context):
        return (
            CanonicalResource(
                namespace="environment",
                kind="file",
                identifier=f"{connection}:/work",
                approval_revision=f"{backing}:/work",
            ),
        )

    executable = _build(executed, resolver=resources, requires_approval=False)
    policy = _Policy(InvocationPolicyDecision.require_approval(), [])
    suspended = await _suspend(executable, policy)
    connection = "mount-resumed"
    if replacement:
        backing = "env-shared:2"
    # Even a newly permissive policy cannot revive an approval for another target.
    result = await executable.run(
        previous_state=suspended.state,
        deferred_resume=DeferredToolResume(suspended.deferred, suspended.deferred.build_results(approve_all=True)),
        bindings=RunBindings.embedded(
            capabilities=(InvocationPolicyCapability(evaluator=_Policy(InvocationPolicyDecision.allow(), [])),)
        ),
    )
    assert result.status == "completed"
    assert executed == ([] if replacement else [1]), (result.output, suspended.deferred)
    if replacement:
        assert "Approved resources changed" in result.output


@pytest.mark.parametrize("kind", ["file", "mount"])
@pytest.mark.parametrize("change", ["connection", "path", "arguments", "deny"])
async def test_environment_approval_ignores_connection_identity(kind, change):
    from a13n_harness.environment._resources import selection_resource
    from a13n_harness.environment.models import EnvironmentPath
    from a13n_harness.environment.providers import FileScopeSelection

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
    # Mount resources do not bind a cwd. File resources still bind the selected path.
    allowed = change == "connection" or (kind == "mount" and change == "path")
    assert executed == ([1] if allowed else [])
