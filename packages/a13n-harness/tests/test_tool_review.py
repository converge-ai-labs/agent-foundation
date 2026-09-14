from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import a13n_harness.models.inference as inference_module
import pytest
from a13n_harness import (
    AbstractHarnessPlugin,
    AgentContext,
    AgentSpec,
    DeferredToolResume,
    DefinitionError,
    HarnessBuilder,
    RunBindings,
)
from a13n_harness.capabilities import (
    AgentToolReviewer,
    ToolReviewAssessment,
    ToolReviewConfig,
    ToolReviewer,
    ToolReviewError,
    ToolReviewRequest,
    ToolReviewResult,
    ToolRiskLevel,
)
from a13n_harness.tools import (
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
    ToolPermissions,
    ToolPermissionsCapability,
)
from a13n_harness.usage import (
    ProviderUsage,
    ProviderUsageRecord,
    UsageMeasure,
)
from pydantic_ai import ToolApproved
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelRequest, SystemPromptPart, ToolReturnPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.providers import Provider

pytestmark = pytest.mark.anyio


def _tool_model(arguments: Mapping[str, Any]) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returned = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returned:
            yield {
                0: DeltaToolCall(
                    name="shell_exec",
                    json_args=json.dumps(dict(arguments)),
                    tool_call_id="shell-call-1",
                )
            }
        else:
            yield "done"

    return FunctionModel(stream_function=stream)


def _metadata() -> HarnessToolMetadata:
    return HarnessToolMetadata(
        tool_id="environment.shell_exec",
        effects=frozenset({"read", "write", "delete", "execute", "external_communication"}),
        credential_audiences=(),
        idempotency="none",
        output_policy=ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096),
    )


@dataclass
class _Policy:
    decision: InvocationPolicyDecision
    calls: int = 0

    async def __call__(self, invocation, metadata, *, context):
        del invocation, metadata, context
        self.calls += 1
        return self.decision


@dataclass
class _Reviewer:
    outcomes: list[ToolReviewResult | Exception]
    requests: list[ToolReviewRequest] = field(default_factory=list)

    async def review(self, request: ToolReviewRequest, *, context: AgentContext) -> ToolReviewResult:
        del context
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _result(risk: ToolRiskLevel, *, usage: tuple[ProviderUsage, ...] = ()) -> ToolReviewResult:
    return ToolReviewResult(
        assessment=ToolReviewAssessment(risk=risk, reason=f"{risk.value} command"),
        usage=usage,
    )


def _build(
    reviewer: ToolReviewer,
    executed: list[dict[str, Any]],
    *,
    risk_threshold: ToolRiskLevel = ToolRiskLevel.HIGH,
    on_flagged: str = "approval_required",
    on_error: str = "approval_required",
    timeout_seconds: float = 120.0,
):
    def shell_exec(
        command: str,
        *,
        cwd: str | None = None,
        environment: Mapping[str, str] | None = None,
        yield_time_seconds: float | None = None,
        execution_timeout_seconds: float | None = None,
        alias: str | None = None,
    ) -> dict[str, bool]:
        executed.append(
            {
                "command": command,
                "cwd": cwd,
                "environment": dict(environment or {}),
                "yield_time_seconds": yield_time_seconds,
                "execution_timeout_seconds": execution_timeout_seconds,
                "alias": alias,
            }
        )
        return {"ok": True}

    return HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_tool_model(
            {
                "command": "printf safe",
                "cwd": "/workspace",
                "environment": {"TOKEN": "secret-value", "PATH": "/bin"},
                "yield_time_seconds": 10,
                "execution_timeout_seconds": 10,
                "alias": "primary",
            }
        ),
        capabilities=(
            Capability(tools=[HarnessTool(shell_exec, harness_metadata=_metadata())], id="shell-tools"),
            ToolPermissionsCapability(
                ToolPermissions(rules={"environment.shell_exec": "review"}),
                review=ToolReviewConfig(
                    model="logical:review-model",
                    risk_threshold=risk_threshold,
                    on_flagged=on_flagged,
                    on_error=on_error,
                    timeout_seconds=timeout_seconds,
                ),
                reviewer=reviewer,
            ),
        ),
    )


def test_agent_spec_registers_shell_review_without_global_registry_mutation() -> None:
    schema = AgentSpec.model_json_schema_with_capabilities()
    variants = schema["properties"]["capabilities"]["items"]["anyOf"]
    assert {"$ref": "#/$defs/spec_ToolPermissionsCapability"} in variants

    spec = AgentSpec(
        capabilities=[
            {
                "name": "ToolPermissionsCapability",
                "arguments": {
                    "review": {
                        "model": "logical:review-model",
                        "risk_threshold": "extra_high",
                        "on_flagged": "deny",
                    }
                },
            }
        ]
    )
    executable = HarnessBuilder().build(spec, output_type=str, model=FunctionModel(lambda messages, info: "done"))
    leaves: list[Any] = []
    executable._agent.root_capability.apply(leaves.append)
    capability = next(item for item in leaves if isinstance(item, ToolPermissionsCapability))

    assert capability.config.model == "logical:review-model"
    assert capability.config.timeout_seconds == 120.0
    assert capability.policy.risk_threshold == ToolRiskLevel.EXTRA_HIGH
    assert capability.policy.on_flagged == "deny"


def test_agent_spec_rejects_duplicate_shell_review_and_run_source_injection() -> None:
    duplicate = AgentSpec(
        capabilities=[
            {"name": "ToolPermissionsCapability", "arguments": {"review": {"model": "logical:one"}}},
            {"name": "ToolPermissionsCapability", "arguments": {"review": {"model": "logical:two"}}},
        ]
    )
    with pytest.raises(DefinitionError) as duplicate_error:
        HarnessBuilder().build(duplicate, output_type=str, model=FunctionModel(lambda messages, info: "done"))
    assert duplicate_error.value.code == "capability_id_duplicate"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(lambda messages, info: "done"),
    )
    with pytest.raises(DefinitionError) as source_error:
        executable.stream(
            "go",
            bindings=RunBindings.embedded(
                capabilities=(ToolPermissionsCapability(review=ToolReviewConfig(model="logical:review")),)
            ),
        )
    assert source_error.value.code == "capability_scope_invalid"


async def test_shell_review_model_uses_builder_gateway_provider_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes: list[tuple[str, str]] = []

    async def review_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[DeltaToolCalls]:
        del messages
        yield {
            0: DeltaToolCall(name=info.output_tools[0].name, json_args='{"risk":"low","reason":"read-only command"}')
        }

    reviewer_model = FunctionModel(stream_function=review_stream, model_name="review-model")
    sentinel_provider = cast(Provider[Any], object())

    def provider_factory(gateway_name: str, provider_name: str) -> Provider[Any]:
        routes.append((gateway_name, provider_name))
        return sentinel_provider

    def fake_infer(
        model: Model | str,
        provider_factory: Callable[[str], Provider[Any]],
    ) -> Model:
        assert model == "openai-responses:gpt-5.6-luna"
        assert provider_factory("openai-responses") is sentinel_provider
        return reviewer_model

    monkeypatch.setattr(inference_module, "_pydantic_infer_model", fake_infer)
    executed: list[dict[str, Any]] = []

    def shell_exec(command: str) -> dict[str, bool]:
        executed.append({"command": command})
        return {"ok": True}

    executable = HarnessBuilder(gateway_provider_factory=provider_factory).build(
        AgentSpec(),
        output_type=str,
        model=_tool_model({"command": "printf safe"}),
        capabilities=(
            Capability(tools=[HarnessTool(shell_exec, harness_metadata=_metadata())], id="shell-tools"),
            ToolPermissionsCapability(
                ToolPermissions(rules={"environment.shell_exec": "review"}),
                review=ToolReviewConfig(model="company@openai:gpt-5.6-luna"),
            ),
        ),
    )
    result = await executable.run(
        "go",
        bindings=RunBindings.embedded(
            capabilities=(InvocationPolicyCapability(evaluator=_Policy(InvocationPolicyDecision.allow())),)
        ),
    )

    assert result.status == "completed"
    assert routes == [("company", "openai-responses")]
    assert executed == [{"command": "printf safe"}]


@pytest.mark.parametrize("risk", list(ToolRiskLevel))
async def test_default_reviewer_uses_only_output_tool_with_auto_choice_and_records_one_request_usage(
    risk: ToolRiskLevel,
) -> None:
    seen_info: list[AgentInfo] = []
    seen_prompts: list[str] = []

    async def review_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[DeltaToolCalls]:
        seen_prompts.extend(
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, SystemPromptPart)
        )
        seen_info.append(info)
        yield {
            0: DeltaToolCall(
                name=info.output_tools[0].name,
                json_args=json.dumps({"risk": risk.value, "reason": "concrete command risk signal"}),
            )
        }

    settings = {"openai_store": False, "temperature": 0.5}
    reviewer = AgentToolReviewer(
        FunctionModel(stream_function=review_model),
        config=ToolReviewConfig(model="test:review", model_settings=cast(Any, settings)),
    )
    result = await reviewer.review(
        ToolReviewRequest(
            tool_id="environment.shell_exec",
            tool_call_id="call-1",
            tool_name="shell_exec",
            profile="shell",
            parameters_schema={},
            arguments={"command": "touch result.txt"},
        ),
        context=cast(AgentContext, object()),  # The default reviewer intentionally ignores Harness context.
    )

    assert result.assessment.risk == risk
    assert len(seen_info) == 1
    assert seen_info[0].function_tools == []
    assert len(seen_info[0].output_tools) == 1
    output_tool = seen_info[0].output_tools[0]
    assert output_tool.name == "submit_tool_review"
    assert "Call this tool exactly once with risk and a brief reason" in output_tool.description
    assert "does not execute or authorize the command" in output_tool.description
    assert "Plain text or JSON text is not a valid submission" in output_tool.description
    assert set(output_tool.parameters_json_schema["required"]) == {"risk", "reason"}
    assert len(seen_prompts) == 1
    assert "provided output tool" in seen_prompts[0]
    assert "Plain text is not an assessment" in seen_prompts[0]
    assert seen_info[0].allow_text_output is True  # Provider compatibility, not local text acceptance.
    assert seen_info[0].model_settings == {"openai_store": False, "temperature": 0.5, "tool_choice": "auto"}
    assert settings == {"openai_store": False, "temperature": 0.5}  # Never mutate caller settings.
    assert result.usage
    assert any(measure.unit == "requests" for measure in result.usage[0].measures)


@pytest.mark.parametrize("output", ["not a structured assessment", '{"risk":"low","reason":"looks valid"}'])
async def test_default_reviewer_preserves_usage_and_rejects_text_output(output: str) -> None:
    async def invalid_review(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield output

    reviewer = AgentToolReviewer(
        FunctionModel(stream_function=invalid_review), config=ToolReviewConfig(model="test:review")
    )
    with pytest.raises(ToolReviewError) as error:
        await reviewer.review(
            ToolReviewRequest(
                tool_id="environment.shell_exec",
                tool_call_id="call-1",
                tool_name="shell_exec",
                profile="shell",
                parameters_schema={},
                arguments={"command": "touch result.txt"},
            ),
            context=cast(AgentContext, object()),
        )

    assert error.value.usage
    measures = {measure.unit: measure.quantity for measure in error.value.usage[0].measures}
    assert measures["requests"] == 1


@pytest.mark.parametrize(
    "failure",
    [
        ModelHTTPError(400, "review-model", {"error": "private-provider-body"}),
        ValueError("private-provider-body"),
    ],
    ids=["http-400", "invalid-response"],
)
async def test_default_reviewer_logs_safe_failure_metadata(
    failure: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    async def failed_review(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        raise failure
        yield ""  # Keep the failing model on the streaming path.

    reviewer = AgentToolReviewer(
        FunctionModel(stream_function=failed_review), config=ToolReviewConfig(model="test:review")
    )
    with pytest.raises(ToolReviewError) as error:
        await reviewer.review(
            ToolReviewRequest(
                tool_id="environment.shell_exec",
                tool_call_id="review-failure-call",
                tool_name="shell_exec",
                profile="shell",
                parameters_schema={},
                arguments={"command": "echo private-command-value"},
            ),
            context=cast(AgentContext, object()),
        )

    assert error.value.code == "tool_review_failed"
    assert error.value.__cause__ is failure
    assert "private-provider-body" not in caplog.text
    assert "private-command-value" not in caplog.text


async def test_policy_deny_still_blocks_dispatch_after_front_review() -> None:
    reviewer = _Reviewer([_result(ToolRiskLevel.LOW)])
    executed: list[dict[str, Any]] = []
    executable = _build(reviewer, executed)
    policy = _Policy(InvocationPolicyDecision.deny("blocked"))

    result = await executable.run(
        "go",
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=policy),)),
    )

    assert result.status == "completed"
    assert policy.calls == 1
    assert len(reviewer.requests) == 1
    assert executed == []


async def test_below_threshold_dispatches_without_environment_values_and_attributes_usage() -> None:
    receipt = ProviderUsage(
        usage_id="shell-review-usage-1",
        provider="review-provider",
        product="review-model",
        timestamp=datetime.now(UTC),
        measures=(UsageMeasure(unit="requests", quantity=Decimal(1)),),
    )
    reviewer = _Reviewer([_result(ToolRiskLevel.MEDIUM, usage=(receipt,))])
    executed: list[dict[str, Any]] = []
    executable = _build(reviewer, executed)

    result = await executable.run(
        "go",
        bindings=RunBindings.embedded(
            capabilities=(InvocationPolicyCapability(evaluator=_Policy(InvocationPolicyDecision.allow())),)
        ),
    )

    assert result.status == "completed"
    assert len(reviewer.requests) == 1
    request = reviewer.requests[0]
    assert request.arguments["environment"] == {"keys": ["PATH", "TOKEN"]}
    assert "secret-value" not in request.model_dump_json()
    assert request.arguments["command"] == "printf safe"
    assert request.arguments["cwd"] == "/workspace"
    assert request.arguments["yield_time_seconds"] == 10
    assert request.arguments["alias"] == "primary"
    assert executed == [
        {
            "command": "printf safe",
            "cwd": "/workspace",
            "environment": {"TOKEN": "secret-value", "PATH": "/bin"},
            "yield_time_seconds": 10.0,
            "execution_timeout_seconds": 10.0,
            "alias": "primary",
        }
    ]
    provider_record = next(item for item in result.usage_records if isinstance(item, ProviderUsageRecord))
    assert provider_record.source == "tool.review"
    assert provider_record.tool_id == "environment.shell_exec"
    assert provider_record.tool_call_id == "shell-call-1"


async def test_flagged_deny_outranks_policy_approval() -> None:
    reviewer = _Reviewer([_result(ToolRiskLevel.HIGH)])
    executed: list[dict[str, Any]] = []
    executable = _build(reviewer, executed, on_flagged="deny")

    result = await executable.run(
        "go",
        bindings=RunBindings.embedded(
            capabilities=(
                InvocationPolicyCapability(
                    evaluator=_Policy(InvocationPolicyDecision.require_approval("confirm command"))
                ),
            )
        ),
    )

    assert result.status == "completed"
    assert executed == []


@pytest.mark.parametrize("error", [ToolReviewError("review_failed"), RuntimeError("unexpected")])
async def test_review_error_uses_configured_fail_closed_action(error: Exception) -> None:
    reviewer = _Reviewer([error])
    executed: list[dict[str, Any]] = []
    executable = _build(reviewer, executed, on_error="deny")

    result = await executable.run(
        "go",
        bindings=RunBindings.embedded(
            capabilities=(InvocationPolicyCapability(evaluator=_Policy(InvocationPolicyDecision.allow())),)
        ),
    )

    assert result.status == "completed"
    assert executed == []


@pytest.mark.parametrize(
    "outcome",
    [
        ToolReviewError("tool_review_failed"),
        ValueError("invalid assessment"),
    ],
    ids=["failed", "invalid"],
)
@pytest.mark.parametrize("policy_action", ["allow", "approval_required", "deny"])
async def test_skip_adds_no_restriction_and_preserves_invocation_policy(
    outcome: ToolReviewResult | Exception, policy_action: str
) -> None:
    reviewer = _Reviewer([outcome])
    executed: list[dict[str, Any]] = []
    executable = _build(reviewer, executed, on_error="allow")
    decision = (
        InvocationPolicyDecision.allow()
        if policy_action == "allow"
        else InvocationPolicyDecision.require_approval("confirm command", metadata={"policy_token": "p-1"})
        if policy_action == "approval_required"
        else InvocationPolicyDecision.deny("blocked")
    )
    policy = _Policy(decision)
    result = await executable.run(
        "go",
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=policy),)),
    )

    assert policy.calls == 1
    assert len(reviewer.requests) == 1
    assert len(executed) == (1 if policy_action == "allow" else 0)
    if policy_action == "approval_required":
        assert result.status == "suspended"
        assert result.deferred is not None
        metadata = result.deferred.metadata["shell-call-1"]
        assert metadata["policy_token"] == "p-1"
        assert "a13n.harness.shell-review" not in metadata
    else:
        assert result.status == "completed"
        assert result.deferred is None


async def test_skip_on_error_does_not_change_flagged_default() -> None:
    reviewer = _Reviewer([_result(ToolRiskLevel.EXTRA_HIGH)])
    executed: list[dict[str, Any]] = []
    executable = _build(reviewer, executed, on_error="allow")

    result = await executable.run("go", bindings=RunBindings.embedded())

    assert result.status == "suspended"
    assert result.deferred is not None
    metadata = result.deferred.metadata["shell-call-1"]["a13n.harness.tool-approval"]
    assert metadata["requested_sources"] == ["reviewer"]
    assert executed == []


async def test_flagged_review_and_policy_require_separate_approvals() -> None:
    reviewer = _Reviewer([_result(ToolRiskLevel.HIGH) for _ in range(3)])
    executed: list[dict[str, Any]] = []
    executable = _build(reviewer, executed)
    first_policy = _Policy(
        InvocationPolicyDecision.require_approval(
            "confirm command",
            metadata={"policy_token": "p-1"},
        )
    )

    first = await executable.run(
        "go",
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=first_policy),)),
    )
    assert first.status == "suspended"
    assert first.state is not None and first.deferred is not None
    call_id = first.deferred.approvals[0].tool_call_id
    metadata = first.deferred.metadata[call_id]
    assert first_policy.calls == 0
    assert metadata["a13n.harness.tool-approval"]["requested_sources"] == ["reviewer"]

    verified: list[dict[str, Any]] = []

    @dataclass
    class _Verifier:
        async def verify(self, invocation, approval_metadata, *, context):
            del invocation, context
            verified.append(dict(approval_metadata))
            return True

    second_policy = _Policy(
        InvocationPolicyDecision.require_approval(
            "confirm command",
            metadata={"policy_token": "p-2"},
        )
    )
    second = await executable.run(
        bindings=RunBindings.embedded(
            capabilities=(
                InvocationPolicyCapability(
                    evaluator=second_policy,
                    approval_verifier=_Verifier(),
                ),
            )
        ),
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            first.deferred,
            first.deferred.build_results(
                approvals={call_id: ToolApproved()},
                metadata={call_id: metadata},
            ),
        ),
    )

    assert second.status == "suspended"
    assert second.deferred is not None
    assert verified == [] and executed == []
    assert second.deferred.metadata[call_id]["a13n.harness.tool-approval"]["requested_sources"] == ["permission"]
    third = await executable.run(
        bindings=RunBindings.embedded(
            capabilities=(InvocationPolicyCapability(evaluator=second_policy, approval_verifier=_Verifier()),)
        ),
        previous_state=second.state,
        deferred_resume=DeferredToolResume(
            second.deferred,
            second.deferred.build_results(
                approvals={call_id: ToolApproved()},
                metadata=second.deferred.metadata,
            ),
        ),
    )
    assert third.status == "completed"
    assert len(reviewer.requests) == 3
    assert verified == [{"policy_token": "p-2"}]
    assert len(executed) == 1


def test_plugin_cannot_contribute_reserved_shell_review_capability() -> None:
    class _Plugin(AbstractHarnessPlugin):
        @property
        def plugin_id(self) -> str:
            return "shell-review-injector"

        def get_capabilities(self):
            return (ToolPermissionsCapability(review=ToolReviewConfig(model="logical:review")),)

    with pytest.raises(DefinitionError) as error:
        HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(lambda messages, info: "done"),
            plugins=(_Plugin(),),
        )

    assert error.value.code == "capability_scope_invalid"
    assert error.value.details["source"] == "plugin"


@pytest.mark.parametrize("on_error", ["deny", "approval_required", "allow"])
@pytest.mark.parametrize("policy_requires_approval", [False, True])
async def test_timeout_denies_even_when_policy_requires_approval_and_preserves_usage(
    on_error, policy_requires_approval: bool
) -> None:
    receipt = ProviderUsage(
        usage_id="review-timeout-usage",
        provider="review-provider",
        product="review-model",
        timestamp=datetime.now(UTC),
        measures=(UsageMeasure(unit="requests", quantity=Decimal(1)),),
    )
    executed = []
    executable = _build(
        _Reviewer([ToolReviewError("tool_review_timeout", usage=(receipt,))]), executed, on_error=on_error
    )
    result = await executable.run(
        "go",
        bindings=RunBindings.embedded(
            capabilities=(
                InvocationPolicyCapability(
                    evaluator=_Policy(
                        InvocationPolicyDecision.require_approval("confirm")
                        if policy_requires_approval
                        else InvocationPolicyDecision.allow()
                    ),
                ),
            )
        ),
    )
    assert result.status == "completed"
    assert result.deferred is None
    assert executed == []
    records = [item for item in result.usage_records if isinstance(item, ProviderUsageRecord)]
    assert len(records) == 1 and records[0].source == "tool.review"


async def test_custom_reviewer_deadline_cancels_review_and_never_dispatches() -> None:
    cancelled = asyncio.Event()

    class WaitingReviewer:
        async def review(self, request, *, context):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    executed = []
    result = await _build(WaitingReviewer(), executed, timeout_seconds=0.01).run("go")
    assert result.status == "completed"
    assert result.deferred is None and executed == []
    assert cancelled.is_set()


async def test_review_cancellation_propagates_without_dispatch() -> None:
    entered = asyncio.Event()

    class WaitingReviewer:
        async def review(self, request, *, context):
            entered.set()
            await asyncio.Event().wait()

    executed = []
    task = asyncio.create_task(_build(WaitingReviewer(), executed).run("go"))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert executed == []


async def test_default_reviewer_timeout_preserves_proven_usage() -> None:
    async def slow_review(messages, info):
        yield '{"risk":'
        await asyncio.Event().wait()

    reviewer = AgentToolReviewer(
        FunctionModel(stream_function=slow_review), config=ToolReviewConfig(model="test:review", timeout_seconds=0.01)
    )
    with pytest.raises(ToolReviewError) as error:
        await reviewer.review(
            ToolReviewRequest(
                tool_id="environment.shell_exec",
                tool_call_id="timeout-call",
                tool_name="shell_exec",
                profile="shell",
                parameters_schema={},
                arguments={"command": "echo safe"},
            ),
            context=cast(AgentContext, object()),
        )
    assert error.value.code == "tool_review_timeout"
    assert error.value.usage
    assert any(measure.unit == "requests" for receipt in error.value.usage for measure in receipt.measures)


async def test_timeout_on_approved_resume_still_denies() -> None:
    reviewer = _Reviewer([_result(ToolRiskLevel.HIGH), ToolReviewError("tool_review_timeout")])
    executed = []
    executable = _build(reviewer, executed)
    first = await executable.run("go")
    assert first.deferred is not None and first.state is not None
    call_id = first.deferred.approvals[0].tool_call_id
    result = await executable.run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(
            first.deferred,
            first.deferred.build_results(approvals={call_id: ToolApproved()}, metadata=first.deferred.metadata),
        ),
    )
    assert result.status == "completed"
    assert result.deferred is None and executed == []


async def test_other_review_failure_still_requests_approval_without_assessment() -> None:
    executed = []
    result = await _build(_Reviewer([ToolReviewError("tool_review_failed")]), executed).run("go")
    assert result.status == "suspended"
    assert result.deferred is not None
    call_id = result.deferred.approvals[0].tool_call_id
    assert result.deferred.metadata[call_id]["a13n.harness.tool-approval"]["requested_sources"] == ["reviewer"]
    assert executed == []


async def test_timeout_emits_observable_denial_before_any_authorization() -> None:
    from a13n_harness import HarnessEvent, HarnessExtensionEvent

    executed = []
    executable = _build(_Reviewer([ToolReviewError("tool_review_timeout")]), executed)
    async with executable.stream("go", bindings=RunBindings.embedded()) as stream:
        events = [item async for item in stream]
    invocations = [
        item.event.payload
        for item in events
        if isinstance(item, HarnessEvent)
        and isinstance(item.event, HarnessExtensionEvent)
        and item.event.kind == "invocation"
    ]
    assert invocations == []
    reviews = [
        item.event.payload
        for item in events
        if isinstance(item, HarnessEvent)
        and isinstance(item.event, HarnessExtensionEvent)
        and item.event.kind == "tool"
        and item.event.payload.get("type") == "tool_review_result"
    ]
    assert len(reviews) == 1
    assert reviews[0]["decision"] == "deny"
    assert reviews[0]["error_code"] == "tool_review_timeout"
    assert reviews[0]["tool_call_id"] == "shell-call-1"
    assert executed == []
    assert stream.result is not None and stream.result.status == "completed"


@pytest.mark.parametrize(
    "arguments",
    [
        '{"risk":"unknown","reason":"invalid risk"}',
        '{"risk":"low","reason":""}',
        '{"risk":"low","reason":"unexpected field","allow":true}',
        '{"risk":',
    ],
)
async def test_default_reviewer_rejects_invalid_output_tool_without_retry(arguments: str) -> None:
    requests = 0

    async def invalid_review(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[DeltaToolCalls]:
        nonlocal requests
        del messages
        requests += 1
        yield {0: DeltaToolCall(name=info.output_tools[0].name, json_args=arguments)}

    reviewer = AgentToolReviewer(
        FunctionModel(stream_function=invalid_review), config=ToolReviewConfig(model="test:review")
    )
    with pytest.raises(ToolReviewError) as error:
        await reviewer.review(
            ToolReviewRequest(
                tool_id="environment.shell_exec",
                tool_call_id="invalid-call",
                tool_name="shell_exec",
                profile="shell",
                parameters_schema={},
                arguments={"command": "echo safe"},
            ),
            context=cast(AgentContext, object()),
        )
    assert error.value.code == "tool_review_failed"
    assert requests == 1
    assert error.value.usage
