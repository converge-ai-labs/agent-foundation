from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from a13n_harness import (
    AgentDefinition,
    AgentIdentityRef,
    AgentInstanceContext,
    DefinitionError,
    HarnessBuilder,
    RunBindings,
    SubagentDefinition,
)
from a13n_harness.capabilities import (
    AsyncDelegateRequest,
    AsyncExecutionView,
    AsyncResumeRequest,
    SubagentCancelRequest,
    SubagentCancelResult,
    SubagentCapability,
    SubagentDelegationPlan,
    SubagentExecutionView,
    SubagentInfoRequest,
    SubagentInfoResult,
    SubagentOperator,
    SubagentOperatorContext,
    SubagentSteerRequest,
    SubagentSteerResult,
    SubagentToolCallContext,
    SubagentWaitRequest,
    SubagentWaitResult,
)
from a13n_harness.capabilities.subagents import SUBAGENT_CAPABILITY_ID
from a13n_harness.environment.advanced import EmptyEnvironmentRuntime
from a13n_harness.tools import (
    HARNESS_TOOL_METADATA_KEY,
    InvocationPolicyCapability,
    InvocationPolicyDecision,
)
from a13n_harness.tools.metadata import normalize_harness_tool_metadata
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.usage import UsageLimits

pytestmark = pytest.mark.anyio


async def _allow(*args: Any, **kwargs: Any) -> InvocationPolicyDecision:
    del args, kwargs
    return InvocationPolicyDecision.allow()


class RecordingSubagentOperator(SubagentOperator):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.plans: list[SubagentDelegationPlan] = []
        self.contexts: list[SubagentOperatorContext] = []
        self.tool_calls: list[SubagentToolCallContext | None] = []
        self.requests: list[object] = []

    @staticmethod
    def _view(
        execution_id: str,
        *,
        status: str = "running",
        resumed_from: str | None = None,
        resumable: bool = False,
    ) -> SubagentExecutionView:
        return SubagentExecutionView(
            execution_id=execution_id,
            subagent_name="reviewer",
            child_definition_id="child-v1",
            status=status,
            resumed_from=resumed_from,
            resumable=resumable,
            thread_id="thread-child",
            child_run_id="run-child",
            segment_index=0,
            input="inspect",
        )

    async def delegate(
        self,
        plan: SubagentDelegationPlan,
        request: AsyncDelegateRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> AsyncExecutionView:
        self.calls.append("delegate")
        self.plans.append(plan)
        self.requests.append(request)
        self.tool_calls.append(tool_call)
        return AsyncExecutionView(
            execution_id="execution-1",
            subagent_name="reviewer",
            child_definition_id="child-v1",
            status="running",
        )

    async def info(
        self,
        context: SubagentOperatorContext,
        request: SubagentInfoRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentInfoResult:
        self.calls.append("info")
        self.contexts.append(context)
        self.requests.append(request)
        self.tool_calls.append(tool_call)
        return SubagentInfoResult(
            executions=(self._view(request.execution_id or "execution-1", status="succeeded", resumable=True),),
            execution_offset=request.execution_offset,
            total=1,
        )

    async def wait(
        self,
        context: SubagentOperatorContext,
        request: SubagentWaitRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentWaitResult:
        self.calls.append("wait")
        self.contexts.append(context)
        self.requests.append(request)
        self.tool_calls.append(tool_call)
        return SubagentWaitResult(
            executions=(self._view(request.execution_id or "execution-1", status="succeeded", resumable=True),),
            execution_offset=request.execution_offset,
            total=1,
        )

    async def steer(
        self,
        context: SubagentOperatorContext,
        request: SubagentSteerRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentSteerResult:
        self.calls.append("steer")
        self.contexts.append(context)
        self.requests.append(request)
        self.tool_calls.append(tool_call)
        return SubagentSteerResult(execution_id=request.execution_id, accepted=True, enqueue_id="enqueue-1")

    async def cancel(
        self,
        context: SubagentOperatorContext,
        request: SubagentCancelRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentCancelResult:
        self.calls.append("cancel")
        self.contexts.append(context)
        self.requests.append(request)
        self.tool_calls.append(tool_call)
        return SubagentCancelResult(execution_id=request.execution_id, accepted=True, status="cancelled")

    async def resume(
        self,
        plan: SubagentDelegationPlan,
        request: AsyncResumeRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> AsyncExecutionView:
        self.calls.append("resume")
        self.plans.append(plan)
        self.requests.append(request)
        self.tool_calls.append(tool_call)
        return AsyncExecutionView(
            execution_id="execution-2",
            subagent_name="reviewer",
            child_definition_id="child-v1",
            status="running",
            resumed_from=request.execution_id,
        )


def _child_definition() -> AgentDefinition[str]:
    async def child_model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        yield "child"

    return AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="child-v1",
        model=FunctionModel(stream_function=child_model),
    )


def _definition(model: FunctionModel, capability: SubagentCapability) -> AgentDefinition[str]:
    return AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="parent-v1",
        model=model,
        capabilities=(capability,),
        subagents=(
            SubagentDefinition(
                name="reviewer",
                description="Review one bounded task.",
                agent=_child_definition(),
                usage_limits=UsageLimits(request_limit=5, total_tokens_limit=80_000),
            ),
        ),
    )


def _bindings() -> RunBindings:
    return RunBindings(
        instance=AgentInstanceContext(
            identity=AgentIdentityRef(issuer="test", subject="parent", tenant="tenant-1"),
            agent_instance_id="agent-parent",
            host_refs={"session_id": "session-parent"},
        ),
        environment=EmptyEnvironmentRuntime(),
        capabilities=(InvocationPolicyCapability(evaluator=_allow),),
        metadata={"request": "request-1"},
    )


def test_subagent_capability_requires_one_coherent_mode() -> None:
    operator = RecordingSubagentOperator()

    default = SubagentCapability()
    assert default.async_enabled is False
    assert default.operator is None

    enabled = SubagentCapability(async_enabled=True, operator=operator)
    assert enabled.async_enabled is True
    assert enabled.operator is operator

    with pytest.raises(DefinitionError) as missing:
        SubagentCapability(async_enabled=True)
    assert missing.value.code == "subagent_operator_required"

    with pytest.raises(DefinitionError) as unexpected:
        SubagentCapability(operator=operator)
    assert unexpected.value.code == "subagent_operator_unexpected"


@pytest.mark.parametrize(
    ("async_enabled", "expected"),
    [
        (False, {"delegate", "resume_subagent"}),
        (
            True,
            {
                "delegate",
                "subagent_info",
                "wait_subagent",
                "steer_subagent",
                "cancel_subagent",
                "resume_subagent",
            },
        ),
    ],
)
async def test_subagent_modes_expose_mutually_exclusive_standard_tools(
    async_enabled: bool,
    expected: set[str],
) -> None:
    operator = RecordingSubagentOperator()
    observed: set[str] = set()

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages
        observed.update(tool.name for tool in info.function_tools)
        if async_enabled:
            wait = next(tool for tool in info.function_tools if tool.name == "wait_subagent")
            assert wait.metadata is not None
            metadata = normalize_harness_tool_metadata(wait.metadata[HARNESS_TOOL_METADATA_KEY])
            assert metadata.output_policy.overflow == "spill"
        yield "done"

    capability = SubagentCapability(async_enabled=True, operator=operator) if async_enabled else SubagentCapability()
    result = (
        await HarnessBuilder()
        .build(_definition(FunctionModel(stream_function=model), capability))
        .run(
            "inspect tools",
            bindings=_bindings(),
        )
    )

    assert result.output_or_raise() == "done"
    assert observed == expected


async def test_async_subagent_toolset_dispatches_complete_host_use_cases() -> None:
    operator = RecordingSubagentOperator()
    steps = (
        ("delegate", {"subagent_name": "reviewer", "prompt": "inspect"}),
        ("subagent_info", {"execution_id": "execution-1"}),
        ("wait_subagent", {"execution_id": "execution-1", "timeout_seconds": 0.01}),
        ("steer_subagent", {"execution_id": "execution-1", "message": "focus"}),
        ("cancel_subagent", {"execution_id": "execution-1"}),
        ("resume_subagent", {"execution_id": "execution-1", "prompt": "continue"}),
    )

    async def parent_model(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if len(returns) < len(steps):
            name, arguments = steps[len(returns)]
            yield {
                0: DeltaToolCall(
                    name=name,
                    json_args=json.dumps(arguments),
                    tool_call_id=f"call-{len(returns) + 1}",
                )
            }
            return
        for returned in returns:
            if returned.tool_name in {"subagent_info", "wait_subagent"}:
                assert returned.content["executions"][0]["reply_tool"] == "resume_subagent"
                assert returned.content["executions"][0]["header"] == (
                    "Subagent result · reviewer · execution_id=execution-1 · succeeded"
                )
        yield "done"

    executable = HarnessBuilder().build(
        _definition(
            FunctionModel(stream_function=parent_model),
            SubagentCapability(async_enabled=True, operator=operator),
        )
    )
    result = await executable.run(
        "parent objective",
        bindings=_bindings(),
        usage_limits=UsageLimits(request_limit=9, total_tokens_limit=70_000),
    )

    assert result.output_or_raise() == "done"
    assert operator.calls == ["delegate", "info", "wait", "steer", "cancel", "info", "resume"]
    assert [item.tool_call_id if item is not None else None for item in operator.tool_calls] == [
        "call-1",
        "call-2",
        "call-3",
        "call-4",
        "call-5",
        "call-6",
        "call-6",
    ]
    assert [item.tool_name if item is not None else None for item in operator.tool_calls] == [
        "delegate",
        "subagent_info",
        "wait_subagent",
        "steer_subagent",
        "cancel_subagent",
        "resume_subagent",
        "resume_subagent",
    ]
    assert len(operator.plans) == 2
    first_plan, resume_plan = operator.plans
    assert first_plan.child.definition.definition_id == "child-v1"
    assert first_plan.child_identity.issuer == "test"
    assert first_plan.child_identity.subject == "parent"
    assert first_plan.child_identity.get_claim("agent_id") == "child-v1"
    assert first_plan.child_identity.get_claim("tenant") == "tenant-1"
    assert first_plan.usage_limits is not None
    assert first_plan.usage_limits.request_limit == 5
    assert first_plan.usage_limits.total_tokens_limit == 80_000
    assert first_plan.parent.parent_thread_id == result.thread_id
    assert first_plan.parent.parent_agent_instance_id == "agent-parent"
    assert dict(first_plan.parent.host_refs) == {"session_id": "session-parent"}
    assert json.loads(first_plan.context.input) == {
        "delegated_task": "inspect",
        "parent_task": "parent objective",
    }
    assert resume_plan.context.input != first_plan.context.input
    assert isinstance(operator.requests[0], AsyncDelegateRequest)
    assert isinstance(operator.requests[-1], AsyncResumeRequest)
    assert result.state is not None
    assert SUBAGENT_CAPABILITY_ID not in result.state.agent_context_state.entries


async def test_parent_exit_does_not_cancel_host_owned_execution() -> None:
    operator = RecordingSubagentOperator()

    async def parent_model(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del info
        returned = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )
        if not returned:
            yield {
                0: DeltaToolCall(
                    name="delegate",
                    json_args='{"subagent_name":"reviewer","prompt":"background"}',
                    tool_call_id="delegate-1",
                )
            }
            return
        yield "accepted"

    result = (
        await HarnessBuilder()
        .build(
            _definition(
                FunctionModel(stream_function=parent_model),
                SubagentCapability(async_enabled=True, operator=operator),
            )
        )
        .run("start", bindings=_bindings())
    )

    assert result.output_or_raise() == "accepted"
    assert operator.calls == ["delegate"]


async def test_async_operator_execution_identity_mismatch_is_rejected() -> None:
    class RetargetingOperator(RecordingSubagentOperator):
        async def delegate(
            self,
            plan: SubagentDelegationPlan,
            request: AsyncDelegateRequest,
            *,
            tool_call: SubagentToolCallContext | None = None,
        ) -> AsyncExecutionView:
            del plan, request, tool_call
            return AsyncExecutionView(
                execution_id="execution-1",
                subagent_name="other",
                child_definition_id="child-v1",
                status="running",
            )

    async def parent_model(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del messages, info
        yield {
            0: DeltaToolCall(
                name="delegate",
                json_args='{"subagent_name":"reviewer","prompt":"inspect"}',
                tool_call_id="delegate-1",
            )
        }

    executable = HarnessBuilder().build(
        _definition(
            FunctionModel(stream_function=parent_model),
            SubagentCapability(async_enabled=True, operator=RetargetingOperator()),
        )
    )
    with pytest.raises(TypeError, match="retargeted"):
        await executable.run("start", bindings=_bindings())


async def test_async_operator_return_type_mismatch_is_rejected() -> None:
    class InvalidOperator(RecordingSubagentOperator):
        async def delegate(  # type: ignore[override]
            self,
            plan: SubagentDelegationPlan,
            request: AsyncDelegateRequest,
            *,
            tool_call: SubagentToolCallContext | None = None,
        ) -> dict[str, str]:
            del plan, request, tool_call
            return {"execution_id": "execution-1"}

    async def parent_model(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> AsyncIterator[str | DeltaToolCalls]:
        del messages, info
        yield {
            0: DeltaToolCall(
                name="delegate",
                json_args='{"subagent_name":"reviewer","prompt":"inspect"}',
                tool_call_id="delegate-1",
            )
        }

    executable = HarnessBuilder().build(
        _definition(
            FunctionModel(stream_function=parent_model),
            SubagentCapability(async_enabled=True, operator=InvalidOperator()),
        )
    )
    with pytest.raises(TypeError, match="must return AsyncExecutionView"):
        await executable.run("start", bindings=_bindings())


@pytest.mark.parametrize(
    "change", [None, "source", "child_definition_id", "subagent_name", "resumed_from", "execution_id"]
)
async def test_host_resume_without_roster_preserves_retained_identity(change: str | None) -> None:
    class HostOperator(RecordingSubagentOperator):
        async def info(self, context, request, *, tool_call=None):
            return SubagentInfoResult(
                executions=(
                    self._view("execution-1", status="succeeded", resumable=True).model_copy(update={"source": "host"}),
                ),
                total=1,
            )

        async def resume_host(self, context, request, *, tool_call=None):
            self.calls.append("resume_host")
            self.requests.append(request)
            self.tool_calls.append(tool_call)
            value = AsyncExecutionView(
                execution_id="execution-2",
                subagent_name="reviewer",
                child_definition_id="child-v1",
                status="running",
                resumed_from="execution-1",
                source="host",
            )
            replacement = "declared" if change == "source" else "execution-1" if change == "execution_id" else "other"
            return value.model_copy(update={change: replacement}) if change else value

    operator = HostOperator()
    observed: set[str] = set()

    async def model(messages, info):
        observed.update(tool.name for tool in info.function_tools)
        if not any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ):
            yield {
                0: DeltaToolCall(
                    name="resume_subagent",
                    json_args='{"execution_id":"execution-1","prompt":"continue"}',
                    tool_call_id="resume-1",
                )
            }
        else:
            yield "done"

    definition = AgentDefinition(
        agent=AgentSpec(),
        output_type=str,
        definition_id="parent-v1",
        model=FunctionModel(stream_function=model),
        capabilities=(SubagentCapability(async_enabled=True, operator=operator),),
    )
    executable = HarnessBuilder().build(definition)
    if change:
        with pytest.raises(TypeError, match="incompatible Host continuation"):
            await executable.run("continue", bindings=_bindings())
    else:
        assert (await executable.run("continue", bindings=_bindings())).output_or_raise() == "done"
        assert "delegate" not in observed and "resume_subagent" in observed
        assert operator.calls == ["resume_host"]
        assert operator.requests == [AsyncResumeRequest(execution_id="execution-1", prompt="continue")]
        assert operator.tool_calls[0].tool_call_id == "resume-1"
        assert not operator.plans


async def test_host_resume_is_opt_in_and_declared_wire_shape_is_unchanged() -> None:
    from pydantic_ai.exceptions import ToolFailed

    operator = RecordingSubagentOperator()
    view = operator._view("execution-1")
    assert "source" not in view.model_dump(mode="json")
    with pytest.raises(ToolFailed, match="does not support"):
        await operator.resume_host(
            SubagentOperatorContext(
                parent_thread_id="parent", parent_run_id="run", parent_agent_instance_id="instance", host_refs={}
            ),
            AsyncResumeRequest(execution_id="execution-1", prompt="continue"),
        )


async def test_async_guidance_describes_automatic_results_and_turn_end():
    operator = RecordingSubagentOperator()
    capability = SubagentCapability(async_enabled=True, operator=operator)

    async def model(messages, info):
        instructions = info.instructions or ""
        assert "Final results are delivered to this conversation automatically" in instructions
        assert "When only background work remains, you can end your turn" in instructions
        if not any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            yield {0: DeltaToolCall(name="delegate", json_args='{"subagent_name":"reviewer","prompt":"inspect"}')}
        else:
            yield "done"

    executable = HarnessBuilder().build(_definition(FunctionModel(stream_function=model), capability))
    result = await executable.run("start", bindings=_bindings())
    assert result.output_or_raise() == "done"


@pytest.mark.parametrize("status", ["running", "succeeded", "failed", "cancelled", "lost"])
@pytest.mark.parametrize("result_type", [SubagentInfoResult, SubagentWaitResult])
def test_execution_page_distinguishes_notifications_from_terminal_results(status, result_type):
    from a13n_harness.toolsets.subagents import _execution_page

    result = result_type(executions=(RecordingSubagentOperator._view("execution-1", status=status),), total=1)
    kind = "Subagent notification" if status == "running" else "Subagent result"
    assert _execution_page(result)["executions"][0]["header"] == (
        f"{kind} · reviewer · execution_id=execution-1 · {status}"
    )
