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
