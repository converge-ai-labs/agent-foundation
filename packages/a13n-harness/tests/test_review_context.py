from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from xml.etree import ElementTree

import pytest
from a13n_harness import AgentContext, AgentSpec, DeferredToolResume, HarnessBuilder, HarnessState, RunBindings
from a13n_harness._review_context import (
    REVIEW_HISTORY_ID,
    ReviewEvidence,
    ReviewHistory,
    append_review_evidence,
    read_review_history,
    select_review_history,
)
from a13n_harness.capabilities import (
    AgentToolReviewer,
    ToolReviewAssessment,
    ToolReviewConfig,
    ToolReviewError,
    ToolReviewPolicy,
    ToolReviewRequest,
    ToolReviewResult,
    ToolReviewRule,
    ToolRiskLevel,
)
from a13n_harness.tools import ToolPermissions, ToolPermissionsCapability
from pydantic_ai import ToolApproved
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.tools import DeferredToolResults
from pydantic_ai.toolsets import FunctionToolset

pytestmark = pytest.mark.anyio


@dataclass
class Reviewer:
    risk: ToolRiskLevel = ToolRiskLevel.LOW
    requests: list[ToolReviewRequest] = field(default_factory=list)
    contexts: list[AgentContext] = field(default_factory=list)

    async def review(self, request, *, context):
        self.requests.append(request)
        self.contexts.append(context)
        return ToolReviewResult(assessment=ToolReviewAssessment(risk=self.risk, reason="Visible risk"))


def _model(name="execute", arguments=None):
    async def stream(messages, info):
        if isinstance(messages[-1], ModelRequest) and any(
            isinstance(part, ToolReturnPart) for part in messages[-1].parts
        ):
            yield "done"
        else:
            yield {0: DeltaToolCall(name=name, json_args=json.dumps(arguments or {}), tool_call_id="call-1")}

    return FunctionModel(stream_function=stream)


_REVIEW_PERMISSIONS = ToolPermissions(default="review")


def _agent(reviewer, execute, *, policy=None, permissions=_REVIEW_PERMISSIONS):
    capabilities = [
        Capability(toolsets=[FunctionToolset([execute], id="business")]),
        ToolPermissionsCapability(permissions, reviewer=reviewer, policy=policy),
    ]
    return HarnessBuilder().build(AgentSpec(), model=_model(), output_type=str, capabilities=tuple(capabilities))


def _history(state):
    return ReviewHistory.model_validate(state.agent_context_state.entries[REVIEW_HISTORY_ID].data)


# Every grade below extra_high allows, so high stands for them at the threshold.
@pytest.mark.parametrize("risk", [ToolRiskLevel.HIGH, ToolRiskLevel.EXTRA_HIGH])
async def test_opted_in_review_only_denies_extra_high_by_default(risk):
    reviewer = Reviewer(risk=risk)
    executed = []

    def execute() -> str:
        executed.append(True)
        return "ok"

    result = await _agent(reviewer, execute).run("Inspect this", bindings=RunBindings.embedded())
    assert result.status == "completed"
    assert len(reviewer.requests) == 1  # Explicit review permission, no shell marker required.
    assert bool(executed) is (risk != ToolRiskLevel.EXTRA_HIGH)
    records = _history(result.state).records
    assert records[0].risk == risk
    assert not records[0].approved
    assert records[0].outcome == "not_executed"
    actions = [record for record in records if record.kind == "action"]
    assert len(actions) == (0 if risk == ToolRiskLevel.EXTRA_HIGH else 1)
    if actions:
        assert actions[0].outcome == "tool_returned"


async def test_reviewer_and_risk_rules_do_not_opt_tools_into_review():
    reviewer = Reviewer(risk=ToolRiskLevel.EXTRA_HIGH)
    executed = []

    def execute() -> str:
        executed.append(True)
        return "ok"

    result = await _agent(
        reviewer,
        execute,
        permissions=None,
        policy=ToolReviewPolicy(rules={"*": ToolReviewRule(risk_threshold="low")}),
    ).run("Go", bindings=RunBindings.embedded())
    assert result.status == "completed"
    assert executed == [True]
    assert not reviewer.requests


def test_review_policy_uses_one_best_match_and_inherits_global_fields():
    policy = ToolReviewPolicy(
        risk_threshold="high",
        on_flagged="deny",
        rules={
            "*": ToolReviewRule(risk_threshold="medium", on_flagged="approval_required"),
            "mcp/business/*": ToolReviewRule(risk_threshold="low"),
            "mcp/business/remove": ToolReviewRule(on_flagged="approval_required"),
        },
    )
    assert policy.decision_for("other", ToolRiskLevel.MEDIUM) == "approval_required"
    assert policy.decision_for("mcp/business/send", ToolRiskLevel.LOW) == "deny"
    # An exact partial rule inherits GLOBAL high, not the broader low threshold.
    assert policy.decision_for("mcp/business/remove", ToolRiskLevel.MEDIUM) == "allow"
    assert policy.decision_for("mcp/business/remove", ToolRiskLevel.HIGH) == "approval_required"
    with pytest.raises(ValueError):
        ToolReviewPolicy(rules={"mcp/*/remove": ToolReviewRule()})


@pytest.mark.parametrize("mode", ["allow", "deny", "ask"])
async def test_explicit_permission_does_not_invoke_risk_reviewer(mode):
    reviewer = Reviewer(risk=ToolRiskLevel.EXTRA_HIGH)

    def execute() -> str:
        return "ok"

    result = await _agent(reviewer, execute, permissions=ToolPermissions(default=mode)).run(
        "Go", bindings=RunBindings.embedded()
    )
    assert not reviewer.requests
    assert result.status == ("suspended" if mode == "ask" else "completed")


async def test_review_history_survives_resume_but_never_grants_authority():
    reviewer = Reviewer(risk=ToolRiskLevel.EXTRA_HIGH)
    executed = []

    def execute() -> str:
        executed.append(True)
        return "large tool return is not review context " * 1000

    agent = _agent(reviewer, execute, policy=ToolReviewPolicy(on_flagged="approval_required"))
    first = await agent.run("Update the workspace", bindings=RunBindings.embedded())
    assert first.status == "suspended" and not executed
    portable = HarnessState.model_validate_json(first.state.model_dump_json())
    resumed = await agent.run(
        previous_state=portable,
        deferred_resume=DeferredToolResume(first.deferred, DeferredToolResults(approvals={"call-1": ToolApproved()})),
        bindings=RunBindings.embedded(),
    )
    assert resumed.status == "completed" and executed == [True]
    request = reviewer.requests[1]
    assert request.approved is True
    assert request.previous_reviews[0].risk == "extra_high"
    assert request.previous_reviews[0].decision == "approval_required"
    assert not request.previous_reviews[0].approved
    assert request.previous_reviews[0].outcome == "not_executed"

    # A new turn sees trajectory, not tool returns, and requests fresh approval.
    later = await agent.run("Now do it again", previous_state=resumed.state, bindings=RunBindings.embedded())
    assert later.status == "suspended" and len(executed) == 1
    request = reviewer.requests[-1]
    assert request.approved is False
    assert any(item.outcome == "tool_returned" for item in request.recent_actions)
    assert "large tool return" not in request.to_prompt()
    assert "Update the workspace" in request.task and "Now do it again" in request.task
    fresh = await agent.run("Separate thread", bindings=RunBindings.embedded())
    assert fresh.status == "suspended"
    assert not reviewer.requests[-1].previous_reviews and not reviewer.requests[-1].recent_actions


async def test_new_deny_policy_wins_over_pending_human_approval():
    reviewer = Reviewer(risk=ToolRiskLevel.EXTRA_HIGH)
    executed = []

    def execute() -> str:
        executed.append(True)
        return "ok"

    first = await _agent(reviewer, execute, policy=ToolReviewPolicy(on_flagged="approval_required")).run(
        "Go", bindings=RunBindings.embedded()
    )
    resumed = await _agent(reviewer, execute).run(
        previous_state=first.state,
        deferred_resume=DeferredToolResume(first.deferred, DeferredToolResults(approvals={"call-1": ToolApproved()})),
        bindings=RunBindings.embedded(),
    )
    assert resumed.status == "completed" and not executed
    assert reviewer.requests[-1].approved is True
    assert _history(resumed.state).records[-1].decision == "deny"


async def test_concurrent_history_writes_are_bounded_flat_and_lossless_within_capacity():
    reviewer = Reviewer()

    def execute() -> str:
        return "ok"

    await _agent(reviewer, execute).run("Go", bindings=RunBindings.embedded())
    context = reviewer.contexts[0]
    await asyncio.gather(
        *(
            append_review_evidence(
                context,
                ReviewEvidence(
                    run_id=context.run_id,
                    tool_call_id=f"call-{i}",
                    tool_id="test",
                    binding=str(i),
                    kind="review",
                ),
            )
            for i in range(40)
        )
    )
    records = (await read_review_history(context)).records
    assert {str(i) for i in range(40)} <= {record.binding for record in records}
    for i in range(40, 60):
        await append_review_evidence(
            context,
            ReviewEvidence(
                run_id=context.run_id,
                tool_call_id=f"call-{i}",
                tool_id="test",
                binding=str(i),
                kind="review",
            ),
        )
    history = await read_review_history(context)
    assert len(history.records) == 48
    assert "arguments" not in history.model_dump_json() and "previous_reviews" not in history.model_dump_json()
    reviews, actions = select_review_history(history, tool_id="test", tool_call_id="call-20", binding="20")
    assert len(reviews) == 5 and reviews[0].tool_call_id == "call-20"
    assert not actions


def test_xml_is_escaped_bounded_and_keeps_current_arguments():
    record = ReviewEvidence(
        run_id="run",
        tool_call_id="old",
        tool_id="test",
        binding="binding",
        kind="review",
        reason="<&" * 200,
    )
    request = ToolReviewRequest(
        tool_id="test",
        tool_call_id="current",
        tool_name="execute",
        arguments={"command": "echo '<task>恶意 & text</task>'", "nested": {'x"<&': "<&"}},
        parameters_schema={"type": "object"},
        task="<&恶意" * 5000,
        previous_reviews=(record,) * 30,
        recent_actions=(record,) * 30,
    )
    prompt = request.to_prompt()
    root = ElementTree.fromstring(prompt)
    assert root.find("current-call/arguments/object/field/string").text == request.arguments["command"]
    assert "task" in root.find("omitted").text
    assert len(prompt.encode()) <= 16 * 1024
    huge = request.model_copy(update={"arguments": {"value": "&" * 14000}})
    with pytest.raises(ToolReviewError, match="tool_review_input_too_large"):
        huge.to_prompt()


async def test_shell_model_receives_same_xml_task_schema_history_and_approval_context(reviewer_context):
    prompts = []

    async def stream(messages, info):
        prompts.extend(
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )
        yield {0: DeltaToolCall(name=info.output_tools[0].name, json_args='{"risk":"low","reason":"Visible command"}')}

    shared = ToolReviewRequest(
        tool_id="environment.shell_exec",
        tool_call_id="current",
        tool_name="shell_exec",
        arguments={"command": "printf '<safe>'"},
        parameters_schema={"type": "object"},
        task="Run a focused check",
        context={"default_mount": "workspace"},
        approved=True,
        previous_reviews=(
            ReviewEvidence(
                run_id="before",
                tool_id="environment.shell_exec",
                tool_call_id="current",
                binding="binding",
                kind="review",
                risk="high",
                decision="approval_required",
                reason="Prior risk",
            ),
        ),
        omitted=("arguments.environment.values",),
        profile="shell",
    )
    request = shared
    result = await AgentToolReviewer(
        FunctionModel(stream_function=stream), config=ToolReviewConfig(model="test:review")
    ).review(
        request,
        context=reviewer_context,
    )
    assert result.assessment.risk == "low"
    assert prompts == [shared.to_prompt()]
    for text in ("Run a focused check", "parameters-schema", "workspace", "approved", "Prior risk"):
        assert text in prompts[0]


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("native", [False, True])
async def test_approval_denial_is_observed_without_reviewer_replay_or_execution(inline, native):
    from pydantic_ai import Tool, ToolDenied
    from pydantic_ai.capabilities import HandleDeferredToolCalls

    reviewer = Reviewer(risk=ToolRiskLevel.LOW if native else ToolRiskLevel.EXTRA_HIGH)
    executed = []

    def execute() -> str:
        executed.append(True)
        return "ok"

    async def deny(ctx, requests):
        return DeferredToolResults(approvals={item.tool_call_id: False for item in requests.approvals})

    capabilities = [
        Capability(toolsets=[FunctionToolset([Tool(execute, requires_approval=native)], id="business")]),
        ToolPermissionsCapability(
            ToolPermissions(default="review"),
            reviewer=reviewer,
            policy=ToolReviewPolicy(on_flagged="approval_required"),
        ),
    ]
    if inline:
        capabilities.append(HandleDeferredToolCalls(deny))
    agent = HarnessBuilder().build(AgentSpec(), model=_model(), output_type=str, capabilities=tuple(capabilities))
    result = await agent.run("Go", bindings=RunBindings.embedded())
    if not inline:
        result = await agent.run(
            previous_state=result.state,
            deferred_resume=DeferredToolResume(
                result.deferred,
                DeferredToolResults(
                    approvals={"call-1": ToolDenied("Not approved")},
                    metadata={"call-1": {"approved_sources": ["permission", "tool"]}},
                ),
            ),
            bindings=RunBindings.embedded(),
        )
    assert result.status == "completed" and not executed
    assert len(reviewer.requests) == 1
    denials = [record for record in _history(result.state).records if record.kind == "approval"]
    assert len(denials) == 1
    assert denials[0].decision == "deny"
    assert not denials[0].approved
    assert denials[0].outcome == "not_executed"


@pytest.mark.parametrize("codeact", [False, True])
async def test_nested_proxy_and_codeact_targets_share_review_and_compact_trajectory(codeact):
    from dataclasses import replace

    from a13n_harness.capabilities import CodeActCapability
    from pydantic_ai.capabilities import AbstractCapability

    class ResponseIdentity(AbstractCapability):
        async def after_model_request(self, ctx, *, request_context, response):
            return replace(response, provider_response_id="resp_nested_parent")

    from .test_tool_proxy import _group, _run

    def double(value: int) -> int:
        return value * 2

    reviewer = Reviewer()
    capabilities = [
        ResponseIdentity(),
        _group(double),
        ToolPermissionsCapability(ToolPermissions(default="review"), reviewer=reviewer),
    ]
    if codeact:
        capabilities.append(CodeActCapability())
        call = ("run_code", {"code": "await call_proxy_tool(group='crm', tool='double', arguments={'value': 3})"})
    else:
        call = ("call_proxy_tool", {"group": "crm", "tool": "double", "arguments": {"value": 3}})
    result, _ = await _run(tuple(capabilities), [call])
    assert result.status == "completed"
    assert len(reviewer.requests) == 2
    assert all(request.source.provider_response_id == "resp_nested_parent" for request in reviewer.requests)
    assert all("resp_nested_parent" not in request.to_prompt() for request in reviewer.requests)
    assert reviewer.requests[-1].tool_id == "tool/native/double"
    assert any(record.outcome == "unknown" for record in reviewer.requests[-1].recent_actions)
    actions = [record for record in _history(result.state).records if record.kind == "action"]
    assert len(actions) == 2 and all(record.outcome == "tool_returned" for record in actions)
    assert {record.tool_id for record in actions} == {request.tool_id for request in reviewer.requests}
    assert "arguments" not in _history(result.state).model_dump_json()


def test_xml_preserves_json_types_and_empty_container_shapes():
    values = [None, "null", "", [], {}, True, "true", 1, "1", {"nested": [None, "null", {}, []]}]
    prompts = []
    for value in values:
        prompt = ToolReviewRequest(
            tool_id="tool/business/execute",
            tool_call_id="call",
            tool_name="execute",
            arguments={"value": value},
            parameters_schema={"type": "object"},
        ).to_prompt()
        ElementTree.fromstring(prompt)
        prompts.append(prompt)
    assert len(set(prompts)) == len(values)
    assert "<null/>" in prompts[0]
    assert "<string>null</string>" in prompts[1]
    assert "<array></array>" in prompts[3]
    assert "<object></object>" in prompts[4]


# An unset mode defaults to allow, so None covers both.
@pytest.mark.parametrize("mode", [None, "deny", "review"])
async def test_identity_wrapper_preserves_external_defaults_and_explicit_modes(mode):
    from a13n_harness.errors import DefinitionError
    from a13n_harness.tools import ToolIdentityToolset
    from pydantic_ai.tools import ToolDefinition
    from pydantic_ai.toolsets.external import ExternalToolset

    tools = ToolIdentityToolset(
        ExternalToolset([ToolDefinition(name="execute", parameters_json_schema={"type": "object"})], id="external"),
        source_id="external",
        default_mode=mode,
    )
    agent = HarnessBuilder().build(
        AgentSpec(),
        model=_model(),
        output_type=str,
        capabilities=(Capability(toolsets=[tools]),),
    )
    if mode == "review":
        with pytest.raises(DefinitionError, match="External tools"):
            await agent.run("Go", bindings=RunBindings.embedded())
    else:
        result = await agent.run("Go", bindings=RunBindings.embedded())
        assert result.status == ("completed" if mode == "deny" else "suspended")
        if mode != "deny":
            assert len(result.deferred.calls) == 1


def test_historical_review_sources_decode_as_advisory_observations():
    record = ReviewEvidence.model_validate(
        {
            "run_id": "old-run",
            "tool_call_id": "old-call",
            "tool_id": "old-tool",
            "binding": "old-binding",
            "kind": "approval",
            "approved_sources": ["permission", "reviewer"],
            "denied_sources": [],
        }
    )
    assert record.approved is True
    assert "approved_sources" not in record.model_dump()
    assert "denied_sources" not in record.model_dump()
