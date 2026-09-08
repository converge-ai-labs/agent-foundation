from __future__ import annotations

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
    AgentShellCommandReviewer,
    ShellReviewAction,
    ShellReviewAssessment,
    ShellReviewCapability,
    ShellReviewError,
    ShellReviewRequest,
    ShellReviewResult,
    ShellRiskLevel,
)
from a13n_harness.tools import (
    HARNESS_TOOL_METADATA_KEY,
    HarnessTool,
    HarnessToolMetadata,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
    ToolOutputPolicy,
)
from a13n_harness.toolsets import ShellToolset
from a13n_harness.usage import (
    ProviderUsage,
    ProviderUsageRecord,
    UsageMeasure,
)
from pydantic_ai import ToolApproved
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
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
        shell_review=True,
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
    outcomes: list[ShellReviewResult | Exception]
    requests: list[ShellReviewRequest] = field(default_factory=list)

    async def review(self, request: ShellReviewRequest, *, context: AgentContext) -> ShellReviewResult:
        del context
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _result(risk: ShellRiskLevel, *, usage: tuple[ProviderUsage, ...] = ()) -> ShellReviewResult:
    return ShellReviewResult(
        assessment=ShellReviewAssessment(risk=risk, reason=f"{risk.value} command"),
        usage=usage,
    )


def _build(
    reviewer: _Reviewer,
    executed: list[dict[str, Any]],
    *,
    risk_threshold: ShellRiskLevel = ShellRiskLevel.HIGH,
    on_flagged: ShellReviewAction = ShellReviewAction.APPROVAL_REQUIRED,
    on_error: ShellReviewAction = ShellReviewAction.APPROVAL_REQUIRED,
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
            ShellReviewCapability(
                "logical:review-model",
                reviewer=reviewer,
                risk_threshold=risk_threshold,
                on_flagged=on_flagged,
                on_error=on_error,
            ),
        ),
    )


def test_agent_spec_registers_shell_review_without_global_registry_mutation() -> None:
    schema = AgentSpec.model_json_schema_with_capabilities()
    variants = schema["properties"]["capabilities"]["items"]["anyOf"]
    assert {"$ref": "#/$defs/spec_ShellReviewCapability"} in variants

    spec = AgentSpec(
        capabilities=[
            {
                "name": "ShellReviewCapability",
                "arguments": {
                    "model": "logical:review-model",
                    "risk_threshold": "extra_high",
                    "on_flagged": "deny",
                },
            }
        ]
    )
    executable = HarnessBuilder().build(spec, output_type=str, model=FunctionModel(lambda messages, info: "done"))
    leaves: list[Any] = []
    executable._agent.root_capability.apply(leaves.append)
    capability = next(item for item in leaves if isinstance(item, ShellReviewCapability))

    assert capability.model == "logical:review-model"
    assert capability.risk_threshold == ShellRiskLevel.EXTRA_HIGH
    assert capability.on_flagged == ShellReviewAction.DENY


def test_agent_spec_rejects_duplicate_shell_review_and_run_source_injection() -> None:
    duplicate = AgentSpec(
        capabilities=[
            {"name": "ShellReviewCapability", "arguments": {"model": "logical:one"}},
            {"name": "ShellReviewCapability", "arguments": {"model": "logical:two"}},
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
            bindings=RunBindings.embedded(capabilities=(ShellReviewCapability("logical:review"),)),
        )
    assert source_error.value.code == "capability_scope_invalid"


async def test_shell_review_model_uses_builder_gateway_provider_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    routes: list[tuple[str, str]] = []

    async def review_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield '{"risk":"low","reason":"read-only command"}'

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
            ShellReviewCapability("company@openai:gpt-5.6-luna"),
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


async def test_default_reviewer_is_tool_free_and_records_one_request_usage() -> None:
    seen_info: list[AgentInfo] = []

    async def review_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        seen_info.append(info)
        yield '{"risk":"medium","reason":"bounded workspace mutation"}'

    reviewer = AgentShellCommandReviewer(FunctionModel(stream_function=review_model))
    result = await reviewer.review(
        ShellReviewRequest(
            tool_id="environment.shell_exec",
            tool_call_id="call-1",
            command="touch result.txt",
        ),
        context=cast(AgentContext, object()),  # The default reviewer intentionally ignores Harness context.
    )

    assert result.assessment.risk == ShellRiskLevel.MEDIUM
    assert len(seen_info) == 1
    assert seen_info[0].function_tools == []
    assert seen_info[0].output_tools == []
    assert result.usage
    assert any(measure.unit == "requests" for measure in result.usage[0].measures)


async def test_default_reviewer_preserves_usage_when_structured_output_fails() -> None:
    async def invalid_review(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "not a structured assessment"

    reviewer = AgentShellCommandReviewer(FunctionModel(stream_function=invalid_review))
    with pytest.raises(ShellReviewError) as error:
        await reviewer.review(
            ShellReviewRequest(
                tool_id="environment.shell_exec",
                tool_call_id="call-1",
                command="touch result.txt",
            ),
            context=cast(AgentContext, object()),
        )

    assert error.value.usage
    measures = {measure.unit: measure.quantity for measure in error.value.usage[0].measures}
    assert measures["requests"] == 1


async def test_policy_deny_skips_shell_review_and_dispatch() -> None:
    reviewer = _Reviewer([_result(ShellRiskLevel.LOW)])
    executed: list[dict[str, Any]] = []
    executable = _build(reviewer, executed)
    policy = _Policy(InvocationPolicyDecision.deny("blocked"))

    result = await executable.run(
        "go",
        bindings=RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=policy),)),
    )

    assert result.status == "completed"
    assert policy.calls == 1
    assert reviewer.requests == []
    assert executed == []


async def test_below_threshold_dispatches_without_environment_values_and_attributes_usage() -> None:
    receipt = ProviderUsage(
        usage_id="shell-review-usage-1",
        provider="review-provider",
        product="review-model",
        timestamp=datetime.now(UTC),
        measures=(UsageMeasure(unit="requests", quantity=Decimal(1)),),
    )
    reviewer = _Reviewer([_result(ShellRiskLevel.MEDIUM, usage=(receipt,))])
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
    assert request.environment_keys == ("PATH", "TOKEN")
    assert "secret-value" not in request.model_dump_json()
    assert request.command == "printf safe"
    assert request.cwd == "/workspace"
    assert request.yield_time_seconds == 10
    assert request.alias == "primary"
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
    assert provider_record.source == "shell.review"
    assert provider_record.tool_id == "environment.shell_exec"
    assert provider_record.tool_call_id == "shell-call-1"


async def test_flagged_deny_outranks_policy_approval() -> None:
    reviewer = _Reviewer([_result(ShellRiskLevel.HIGH)])
    executed: list[dict[str, Any]] = []
    executable = _build(reviewer, executed, on_flagged=ShellReviewAction.DENY)

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


@pytest.mark.parametrize("error", [ShellReviewError("review_failed"), RuntimeError("unexpected")])
async def test_review_error_uses_configured_fail_closed_action(error: Exception) -> None:
    reviewer = _Reviewer([error])
    executed: list[dict[str, Any]] = []
    executable = _build(reviewer, executed, on_error=ShellReviewAction.DENY)

    result = await executable.run(
        "go",
        bindings=RunBindings.embedded(
            capabilities=(InvocationPolicyCapability(evaluator=_Policy(InvocationPolicyDecision.allow())),)
        ),
    )

    assert result.status == "completed"
    assert executed == []


async def test_flagged_review_combines_metadata_and_reruns_on_approved_resume() -> None:
    reviewer = _Reviewer([_result(ShellRiskLevel.HIGH), _result(ShellRiskLevel.HIGH)])
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
    assert metadata["a13n.harness.invocation-policy"] == {
        "decision": "approval_required",
        "metadata": {"policy_token": "p-1"},
    }
    assert metadata["a13n.harness.shell-review"] == {
        "action": "approval_required",
        "status": "flagged",
        "risk": "high",
        "reason": "high command",
    }

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

    assert second.status == "completed"
    assert len(reviewer.requests) == 2
    assert verified == [{"policy_token": "p-1"}]
    assert len(executed) == 1


def test_plugin_cannot_contribute_reserved_shell_review_capability() -> None:
    class _Plugin(AbstractHarnessPlugin):
        @property
        def plugin_id(self) -> str:
            return "shell-review-injector"

        def get_capabilities(self):
            return (ShellReviewCapability("logical:review"),)

    with pytest.raises(DefinitionError) as error:
        HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(lambda messages, info: "done"),
            plugins=(_Plugin(),),
        )

    assert error.value.code == "capability_scope_invalid"
    assert error.value.details["source"] == "plugin"


def test_shell_toolset_marks_only_command_launch_for_review() -> None:
    toolset = cast(Any, object.__new__(ShellToolset))
    toolset._resource_resolver = lambda tool_id: None
    command = toolset._tool(
        lambda: None,
        "environment.shell_exec",
        {"execute"},
        "none",
        resources=cast(Any, None),
    )
    wait = toolset._tool(
        lambda: None,
        "environment.process_wait",
        {"read"},
        "none",
        resources=cast(Any, None),
    )

    assert command.metadata[HARNESS_TOOL_METADATA_KEY].shell_review is True
    assert wait.metadata[HARNESS_TOOL_METADATA_KEY].shell_review is False
