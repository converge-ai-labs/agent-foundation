from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from a13n_harness import AgentContext, AgentSpec, DeferredToolResume, HarnessBuilder, RunBindings
from a13n_harness.capabilities import ToolReviewAssessment, ToolReviewRequest, ToolReviewResult
from a13n_harness.capabilities.tool_review import ToolReviewPolicy, render_review_instruction
from a13n_harness.tools import ToolIdentity, ToolPermissions, ToolPermissionsCapability, source_tool_id
from a13n_harness.tools.approval import ApprovalPresentation, approval_presentation
from pydantic import ValidationError
from pydantic_ai import RunContext, ToolApproved
from pydantic_ai.capabilities import Capability
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.tools import DeferredToolResults, Tool
from pydantic_ai.toolsets import FunctionToolset

pytestmark = pytest.mark.anyio


def test_approval_presentation_hides_url_credentials_and_command_arguments() -> None:
    assert approval_presentation({"url": "https://user:secret@example.org/page?token=secret#fragment"})["target"] == (
        "url: https://example.org/page"
    )
    assert approval_presentation({"command": "deploy --token secret"})["target"] == "command: [arguments hidden]"
    assert approval_presentation({"command": "deploy"}, reason="review")["reason"] == (
        "Tool reviewer requires approval."
    )
    with pytest.raises(ValidationError):
        ApprovalPresentation.model_validate({"reason": "api_key=secret"})


def _model(name: str = "execute", arguments: dict | None = None) -> FunctionModel:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            yield "done"
        else:
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments or {}), tool_call_id="call-1")}

    return FunctionModel(stream_function=stream)


@dataclass
class _Reviewer:
    risk: str = "low"
    requests: list[ToolReviewRequest] = field(default_factory=list)

    async def review(self, request: ToolReviewRequest, *, context: AgentContext) -> ToolReviewResult:
        del context
        self.requests.append(request)
        return ToolReviewResult(assessment=ToolReviewAssessment(risk=self.risk, reason="test review"))


def test_selector_specificity_and_inherited_defaults() -> None:
    policy = ToolPermissions(default="deny", rules={"environment.*": "ask", "environment.shell_exec": "inherit"})
    assert policy.resolve(ToolIdentity("environment.shell_exec", "review")) == "review"
    assert policy.resolve(ToolIdentity("environment.read")) == "ask"
    assert policy.resolve(ToolIdentity("other")) == "deny"
    assert source_tool_id("a/b", "x*y", kind="mcp") == "mcp/a%2Fb/x%2Ay"
    with pytest.raises(ValueError):
        ToolPermissions(rules={"mcp/*/delete": "deny"})


def test_custom_instruction_is_separately_rendered_text() -> None:
    assert render_review_instruction(None) is None
    assert render_review_instruction("a < b & c") == "<custom-instruction>\na &lt; b &amp; c\n</custom-instruction>"


@pytest.mark.parametrize("mode", ["allow", "review", "deny"])
async def test_native_tools_are_gated_before_custom_validation(mode: str) -> None:
    steps = []

    def validate(ctx: RunContext[AgentContext]) -> None:
        assert ctx.deps.tool_approval is not None
        steps.append("validate")

    def execute(ctx: RunContext[AgentContext]) -> str:
        assert ctx.deps.tool_approval is not None
        assert not ctx.deps.tool_approval.approved_sources
        steps.append("execute")
        return "ok"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=_model(),
        output_type=str,
        capabilities=(
            Capability(toolsets=[FunctionToolset([Tool(execute, args_validator=validate)], id="native-tools")]),
            ToolPermissionsCapability(ToolPermissions(rules={"tool/native-tools/execute": mode})),
        ),
    )
    result = await executable.run("go", bindings=RunBindings.embedded())
    assert result.status == "completed"
    assert steps == ([] if mode == "deny" else ["validate", "execute"])


async def test_permission_then_tool_hitl_preserves_both_approvals() -> None:
    seen = []
    executed = []

    def execute(ctx: RunContext[AgentContext]) -> str:
        approval = ctx.deps.tool_approval
        assert approval is not None
        seen.append(approval.approved_sources)
        if "tool" not in approval.approved_sources:
            raise ApprovalRequired(metadata={"reason": "Confirm business action"})
        executed.append(True)
        return "ok"

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=_model(),
        output_type=str,
        capabilities=(
            Capability(toolsets=[FunctionToolset([execute], id="business")]),
            ToolPermissionsCapability(ToolPermissions(default="ask")),
        ),
    )
    result = await executable.run("go", bindings=RunBindings.embedded())
    assert result.status == "suspended"
    assert not seen
    for source in ("permission", "tool"):
        requests = result.deferred
        assert requests is not None
        assert requests.metadata["call-1"]["a13n.harness.tool-approval"]["requested_sources"] == [source]
        result = await executable.run(
            previous_state=result.state,
            deferred_resume=DeferredToolResume(requests, DeferredToolResults(approvals={"call-1": ToolApproved()})),
            bindings=RunBindings.embedded(),
        )
    assert result.status == "completed"
    assert seen == [frozenset({"permission"}), frozenset({"permission", "tool"})]
    assert executed == [True]


async def test_review_allow_is_not_human_approval_and_runs_before_validator() -> None:
    reviewer = _Reviewer()
    executed = []

    def validate(ctx: RunContext[AgentContext], value: str) -> None:
        assert len(reviewer.requests) == 1
        assert not ctx.deps.tool_approval.approved_sources

    def execute(ctx: RunContext[AgentContext], value: str) -> str:
        executed.append(value)
        return value

    executable = HarnessBuilder().build(
        AgentSpec(),
        model=_model(arguments={"value": "hello"}),
        output_type=str,
        capabilities=(
            Capability(toolsets=[FunctionToolset([Tool(execute, args_validator=validate)], id="business")]),
            ToolPermissionsCapability(
                ToolPermissions(default="review"),
                reviewer=reviewer,
                policy=ToolReviewPolicy(on_flagged="approval_required"),
            ),
        ),
    )
    result = await executable.run("do this", bindings=RunBindings.embedded())
    assert result.status == "completed"
    assert executed == ["hello"]
    assert len(reviewer.requests) == 1
    assert reviewer.requests[0].task == "do this"


async def test_renaming_does_not_change_permission_identity() -> None:
    executed = []

    def execute() -> str:
        executed.append(True)
        return "ok"

    source = FunctionToolset([execute], id="business").renamed({"renamed": "execute"}).prefixed("visible")
    executable = HarnessBuilder().build(
        AgentSpec(),
        model=_model("visible_renamed"),
        output_type=str,
        capabilities=(
            Capability(toolsets=[source]),
            ToolPermissionsCapability(ToolPermissions(rules={"tool/business/execute": "deny"})),
        ),
    )
    result = await executable.run("go", bindings=RunBindings.embedded())
    assert result.status == "completed"
    assert executed == []


async def test_unmatched_reviewer_does_not_limit_arguments() -> None:
    reviewer = _Reviewer()
    seen = []

    def execute(value: str) -> str:
        seen.append(len(value))
        return "ok"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model(arguments={"value": "x" * 66000}),
        capabilities=(
            Capability(toolsets=[FunctionToolset([execute], id="business")]),
            ToolPermissionsCapability(
                ToolPermissions(default="review"), reviewers={"environment.shell_exec": reviewer}
            ),
        ),
    )
    result = await executable.run("go", bindings=RunBindings.embedded())
    assert result.status == "completed"
    assert seen == [66000]
    assert reviewer.requests == []


async def test_inline_native_handler_does_not_need_to_echo_approval_metadata() -> None:
    from pydantic_ai.capabilities import HandleDeferredToolCalls

    seen = []

    def execute(ctx: RunContext[AgentContext]) -> str:
        seen.append(ctx.deps.tool_approval.approved_sources)
        return "ok"

    async def approve(ctx, requests):
        return DeferredToolResults(approvals={item.tool_call_id: ToolApproved() for item in requests.approvals})

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model(),
        capabilities=(
            Capability(toolsets=[FunctionToolset([execute], id="business")]),
            ToolPermissionsCapability(ToolPermissions(default="ask")),
            HandleDeferredToolCalls(approve),
        ),
    )
    result = await executable.run("go", bindings=RunBindings.embedded())
    assert result.status == "completed"
    assert seen == [frozenset({"permission"})]


async def test_reviewer_approval_does_not_approve_managed_policy() -> None:
    from a13n_harness.tools import (
        HarnessTool,
        HarnessToolMetadata,
        InvocationPolicyCapability,
        InvocationPolicyDecision,
        ToolOutputPolicy,
    )

    seen = []
    policy_calls = []
    reviewer = _Reviewer(risk="extra_high")

    def execute(ctx: RunContext[AgentContext]) -> str:
        seen.append(ctx.deps.tool_approval.approved_sources)
        return "ok"

    async def policy(invocation, metadata, *, context):
        policy_calls.append(invocation.tool_id)
        return InvocationPolicyDecision.require_approval("Approve the managed resource")

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model(),
        capabilities=(
            Capability(
                toolsets=[
                    FunctionToolset(
                        [
                            HarnessTool(
                                execute,
                                harness_metadata=HarnessToolMetadata(
                                    tool_id="business.execute",
                                    effects=frozenset({"write"}),
                                    credential_audiences=(),
                                    idempotency="none",
                                    output_policy=ToolOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096),
                                ),
                            )
                        ],
                        id="business",
                    )
                ]
            ),
            ToolPermissionsCapability(
                ToolPermissions(default="review"),
                reviewer=reviewer,
                policy=ToolReviewPolicy(on_flagged="approval_required"),
            ),
        ),
    )

    def bindings():
        return RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=policy),))

    result = await executable.run("go", bindings=bindings())
    assert result.status == "suspended"
    assert not policy_calls
    for source in ("reviewer", "permission"):
        requests = result.deferred
        assert requests is not None
        assert requests.metadata["call-1"]["a13n.harness.tool-approval"]["requested_sources"] == [source]
        assert seen == []
        result = await executable.run(
            previous_state=result.state,
            deferred_resume=DeferredToolResume(
                requests,
                DeferredToolResults(approvals={"call-1": ToolApproved()}),
            ),
            bindings=bindings(),
        )
    assert result.status == "completed"
    assert seen == [frozenset({"reviewer", "permission"})]


@pytest.mark.parametrize("failure", [None, "tool_review_failed", "tool_review_timeout"])
async def test_review_usage_and_result_events_share_call_identity(failure: str | None) -> None:
    from datetime import UTC, datetime
    from decimal import Decimal

    from a13n_harness import HarnessEvent, HarnessExtensionEvent
    from a13n_harness.capabilities import ToolReviewError
    from a13n_harness.usage import ProviderUsage, ProviderUsageRecord, UsageMeasure

    receipt = ProviderUsage(
        usage_id="review-request-1",
        provider="test",
        product="review-model",
        timestamp=datetime.now(UTC),
        measures=(UsageMeasure(unit="input_tokens", quantity=Decimal(17)),),
    )

    class Reviewer:
        async def review(self, request, *, context):
            if failure is not None:
                raise ToolReviewError(failure, usage=(receipt,))
            return ToolReviewResult(assessment=ToolReviewAssessment(risk="low", reason="Safe call"), usage=(receipt,))

    def execute() -> str:
        return "ok"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=_model(),
        capabilities=(
            Capability(toolsets=[FunctionToolset([execute], id="business")]),
            ToolPermissionsCapability(ToolPermissions(default="review"), reviewer=Reviewer()),
        ),
    )
    async with executable.stream("go", bindings=RunBindings.embedded()) as stream:
        events = [item async for item in stream]
    assert stream.result is not None
    usage = [item for item in stream.result.usage_records if isinstance(item, ProviderUsageRecord)]
    assert len(usage) == 1
    assert usage[0].source == "tool.review"
    assert usage[0].tool_id == "tool/business/execute" and usage[0].tool_call_id == "call-1"
    reviews = [
        item.event.payload
        for item in events
        if isinstance(item, HarnessEvent)
        and isinstance(item.event, HarnessExtensionEvent)
        and item.event.kind == "tool"
        and item.event.payload.get("type") == "tool_review_result"
    ]
    assert len(reviews) == 1
    review = reviews[0]
    assert review["tool_id"] == usage[0].tool_id and review["tool_call_id"] == usage[0].tool_call_id
    if failure is None:
        result = ToolReviewResult.model_validate(review["result"])
        assert result.assessment.risk == "low" and result.usage == (receipt,)
    else:
        assert review["status"] == "error" and review["error_code"] == failure
        assert review["decision"] == ("deny" if failure.endswith("timeout") else "approval_required")


async def test_approval_context_is_call_local_and_cleared_after_dispatch() -> None:
    import asyncio

    contexts = []
    entered = []
    ready = asyncio.Event()

    async def execute(ctx: RunContext[AgentContext], value: int) -> str:
        contexts.append(ctx.deps)
        approval = ctx.deps.tool_approval
        entered.append(value)
        if len(entered) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 2)
        assert ctx.deps.tool_approval is approval
        assert approval.tool_call_id == f"call-{value}"
        return "ok"

    async def model(messages, info):
        if any(isinstance(part, ToolReturnPart) for m in messages if isinstance(m, ModelRequest) for part in m.parts):
            yield "done"
        else:
            yield {
                index: DeltaToolCall(
                    name="execute", json_args=json.dumps({"value": index}), tool_call_id=f"call-{index}"
                )
                for index in (1, 2)
            }

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(Capability(toolsets=[FunctionToolset([execute], id="business")]),),
    )
    result = await executable.run("go", bindings=RunBindings.embedded())
    assert result.status == "completed"
    assert len(contexts) == 2 and contexts[0] is contexts[1]
    assert contexts[0].tool_approval is None


@pytest.mark.parametrize("response", ["structured", "text"])
@pytest.mark.parametrize("profile", ["general", "shell"])
async def test_agent_reviewer_keeps_instruction_separate_and_requires_structured_output(response, profile) -> None:
    from typing import cast

    from a13n_harness.capabilities import AgentToolReviewer, ToolReviewConfig, ToolReviewError
    from pydantic_ai.messages import SystemPromptPart

    seen = []
    config = ToolReviewConfig(model="test-review", instruction="General <rule>", shell_instruction="Shell <rule>")

    async def review_model(messages, info):
        seen.append((messages, info))
        if response == "text":
            yield '{"risk":"low","reason":"Looks valid"}'
        else:
            yield {
                0: DeltaToolCall(name=info.output_tools[0].name, json_args='{"risk":"low","reason":"Validated call"}')
            }

    reviewer = AgentToolReviewer(FunctionModel(stream_function=review_model), config)
    request = ToolReviewRequest(
        tool_id="tool/test/execute",
        tool_call_id="test-call",
        tool_name="execute",
        parameters_schema={"type": "object"},
        arguments={},
        profile=profile,
    )
    if response == "text":
        with pytest.raises(ToolReviewError) as caught:
            await reviewer.review(request, context=cast(AgentContext, object()))
        assert caught.value.code == "tool_review_failed"
        assert caught.value.usage
    else:
        result = await reviewer.review(request, context=cast(AgentContext, object()))
        assert result.assessment.risk == "low" and result.usage
    assert len(seen) == 1
    messages, info = seen[0]
    assert info.function_tools == []
    assert [tool.name for tool in info.output_tools] == ["submit_tool_review"]
    assert info.model_settings["tool_choice"] == "auto"
    system = "\n".join(
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, SystemPromptPart)
    )
    assert "General &lt;rule&gt;" not in system and "Shell &lt;rule&gt;" not in system
    instruction = "\n".join(message.instructions or "" for message in messages if isinstance(message, ModelRequest))
    expected = "Shell" if profile == "shell" else "General"
    assert f"<custom-instruction>\n{expected} &lt;rule&gt;\n</custom-instruction>" in instruction


@pytest.mark.parametrize("outcome", ["denied", "changed_arguments", "changed_schema"])
async def test_pending_permission_never_dispatches_denial_or_changed_binding(outcome) -> None:
    from pydantic_ai import ToolDenied

    executed = []

    def execute(value: int) -> str:
        executed.append(value)
        return "ok"

    def build(extra_schema=None):
        tool = Tool(execute)
        if extra_schema is not None:
            tool.function_schema.json_schema["description"] = extra_schema
        return HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=_model(arguments={"value": 1}),
            capabilities=(
                Capability(toolsets=[FunctionToolset([tool], id="business")]),
                ToolPermissionsCapability(ToolPermissions(default="ask")),
            ),
        )

    executable = build()
    first = await executable.run("go", bindings=RunBindings.embedded())
    assert first.status == "suspended" and not executed
    decision = (
        ToolDenied("No")
        if outcome == "denied"
        else ToolApproved(override_args={"value": 2})
        if outcome == "changed_arguments"
        else ToolApproved()
    )
    if outcome == "changed_schema":
        executable = build("Changed schema")
    result = await executable.run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(first.deferred, DeferredToolResults(approvals={"call-1": decision})),
        bindings=RunBindings.embedded(),
    )
    assert result.status == "completed"
    assert not executed


async def test_declarative_permissions_gate_native_tools_without_host_type_registration() -> None:
    from pydantic_ai.agent.spec import CapabilitySpec

    effects = []

    def execute() -> str:
        effects.append(True)
        return "not allowed"

    executable = HarnessBuilder().build(
        AgentSpec(capabilities=[CapabilitySpec(name="ToolPermissionsCapability", arguments={"default": "deny"})]),
        output_type=str,
        model=_model(),
        capabilities=(Capability(toolsets=[FunctionToolset([execute], id="business")]),),
    )
    result = await executable.run("go", bindings=RunBindings.embedded())
    assert result.status == "completed" and not effects
