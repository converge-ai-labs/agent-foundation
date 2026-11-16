from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.capabilities import (
    AsyncExecutionView,
    SubagentCapability,
    SubagentExecutionView,
    SubagentInfoResult,
    SubagentOperator,
)
from a13n_harness.capabilities.execution import ExecutionCapability
from a13n_harness.envelopes import ExecutionRef
from a13n_harness.model_context import ModelContextProjection, ModelContextProjectionRequest, ModelContextRequestKind
from a13n_harness.tool_execution import ToolExecutionRecord
from a13n_harness.toolsets.execution import ExecutionToolset
from pydantic_ai.exceptions import ToolFailed

pytestmark = pytest.mark.anyio


class Operator(SubagentOperator):
    def __init__(self, status="running", resumable=False, output=None):
        self.view = SubagentExecutionView(
            execution_id="agent-1",
            subagent_name="worker",
            child_definition_id="d",
            source="host",
            status=status,
            resumable=resumable,
            output=output,
        )
        self.steer = AsyncMock(return_value=SimpleNamespace())

    async def delegate(self, plan, request, *, tool_call=None):
        raise AssertionError("unexpected delegate")

    async def info(self, context, request, *, tool_call=None):
        return SubagentInfoResult(executions=(self.view,), execution_offset=request.execution_offset, total=1)

    async def wait(self, context, request, *, tool_call=None):
        raise AssertionError("unexpected wait")

    async def steer(self, context, request, *, tool_call=None):
        raise AssertionError("replace with recorded steering")

    async def cancel(self, context, request, *, tool_call=None):
        raise AssertionError("unexpected cancel")

    async def resume(self, plan, request, *, tool_call=None):
        raise AssertionError("Host continuation must not use a declared plan")

    async def resume_host(self, context, request, *, tool_call=None):
        return AsyncExecutionView(
            execution_id="agent-2",
            subagent_name="worker",
            child_definition_id="d",
            source="host",
            status="running",
            resumed_from="agent-1",
        )


def tools(operator=None, router=None, runtime=None):
    owner = ExecutionCapability()
    context = SimpleNamespace(
        thread_id="thread_owner",
        run_id="run-owner",
        instance=SimpleNamespace(agent_instance_id="instance", host_refs={}),
        message_router=router,
        tool_execution=runtime,
    )
    subagents = SubagentCapability(async_enabled=True, operator=operator) if operator else None
    ctx = SimpleNamespace(
        deps=context, capabilities={"a13n.executions": owner}, tool_call_id="call-1", tool_name="send_message"
    )
    return ExecutionToolset(owner=owner, context=context, subagents=subagents), ctx


@pytest.mark.parametrize("target", ["parent", "bot", "live", "thread_target"])
async def test_router_namespace_has_no_operator_fallback(target):
    router = SimpleNamespace(send=AsyncMock(return_value={"accepted": True}))
    toolset, ctx = tools(Operator(), router)
    assert await toolset.send_message(ctx, target, "hello") == {"accepted": True}
    router.send.assert_awaited_once_with(ctx, target, "hello")
    router.send.side_effect = ToolFailed("denied")
    with pytest.raises(ToolFailed, match="denied"):
        await toolset.send_message(ctx, target, "hello")
    toolset, ctx = tools(Operator())
    with pytest.raises(ToolFailed, match="no MessageRouter"):
        await toolset.send_message(ctx, target, "hello")


async def test_execution_namespaces_do_not_fall_back_to_router():
    router = SimpleNamespace(send=AsyncMock())
    operator = Operator()
    operator.info = AsyncMock(side_effect=ToolFailed("not owned"))
    toolset, ctx = tools(operator, router)
    with pytest.raises(ToolFailed, match="not owned"):
        await toolset.send_message(ctx, "agent-1", "hello")
    with pytest.raises(ToolFailed, match="tool executions"):
        await toolset.send_message(ctx, "exec-1", "hello")
    router.send.assert_not_awaited()
    operator.info.assert_awaited_once()


@pytest.mark.parametrize("status", ["queued", "succeeded", "failed", "cancelled", "lost"])
async def test_nonresumable_message_reports_state(status):
    toolset, ctx = tools(Operator(status=status))
    with pytest.raises(ToolFailed, match=status):
        await toolset.send_message(ctx, "agent-1", "hello")


async def test_running_message_and_linked_resume_are_acceptance_only():
    from a13n_harness.capabilities import SubagentSteerResult

    operator = Operator()
    operator.steer.return_value = SubagentSteerResult(execution_id="agent-1", accepted=True)
    toolset, ctx = tools(operator)
    assert (await toolset.send_message(ctx, "agent-1", "hello"))["accepted"]
    assert operator.steer.await_args.args[1].message == "hello"
    operator.steer.return_value = SubagentSteerResult(execution_id="other", accepted=True)
    with pytest.raises(TypeError, match="retargeted"):
        await toolset.send_message(ctx, "agent-1", "hello")
    toolset, ctx = tools(Operator(status="succeeded", resumable=True))
    receipt = await toolset.send_message(ctx, "agent-1", "continue")
    assert receipt["accepted"] and receipt["execution_id"] == "agent-2" and receipt["resumed_from"] == "agent-1"


async def test_agent_output_is_bounded_and_unavailable_is_explicit():
    toolset, ctx = tools(Operator(status="succeeded", output="x" * 40_000))
    result = await toolset.execution_result(ctx, "agent-1")
    assert len(result["output"]) == 32 * 1024 and result["output"].endswith("[truncated]")
    assert Operator(output="not final").view.output is None
    toolset, ctx = tools(Operator(status="succeeded"))
    result = await toolset.execution_result(ctx, "agent-1")
    assert result.return_value["output"] is None and "unavailable" in result.content[0]


async def test_info_has_one_page_per_kind_and_tool_ownership_is_exact():
    runtime = SimpleNamespace(summarize=AsyncMock(return_value=[]), info=AsyncMock(return_value=None))
    toolset, ctx = tools(Operator(), runtime=runtime)
    page = await toolset.execution_info(ctx, offset=2, limit=7)
    assert page["agent_executions"]["execution_offset"] == 2 and page["tool_executions"] == []
    runtime.summarize.assert_awaited_once_with("thread_owner", offset=2, limit=7)
    assert await toolset.execution_info(ctx, "exec-missing") == {"status": "unavailable"}
    with pytest.raises(ToolFailed, match="maximum of 30"):
        await toolset.execution_wait(ctx, "exec-missing", 31)


@pytest.mark.parametrize("state", ["queued", "running", "succeeded", "failed", "cancelled", "lost"])
async def test_background_work_uses_shared_states_for_tools_and_agents(state):
    record = ToolExecutionRecord(
        execution_id="exec-1",
        thread_id="thread_owner",
        run_id="run-owner",
        tool_call_id="call-1",
        tool_name="lookup",
        status=state,
        result_available=state not in {"queued", "running"},
    )
    runtime = SimpleNamespace(summarize=AsyncMock(return_value=[record]))
    operator = Operator(status=state)
    operator.attention = AsyncMock(
        return_value=(ExecutionRef(execution_id="agent-1", kind="agent", name="worker", state=state),)
    )
    toolset, ctx = tools(operator, runtime=runtime)
    ctx.capabilities["a13n.subagents"] = toolset._subagents
    ctx.deps._tool_executions = SimpleNamespace(result_origins={})

    async def handler(request):
        return ModelContextProjection()

    projection = await toolset._owner.wrap_model_context(
        ctx, ModelContextProjectionRequest(kind=ModelContextRequestKind.INPUT), handler
    )
    availability = ", result available" if record.result_available else ""
    assert projection.blocks[0].content.splitlines() == [
        "Background work (2)",
        f"- exec-1 | lookup | {state}{availability}",
        f"- agent-1 | worker | {state}",
    ]


async def test_attention_default_and_merged_limit_without_activity_reads():
    operator = Operator()
    assert await operator.attention(None) == ()
    operator.attention = AsyncMock(
        return_value=tuple(
            ExecutionRef(execution_id=f"agent-{i}", kind="agent", name="worker", state="running") for i in range(21)
        )
    )
    operator.info = AsyncMock(side_effect=AssertionError("reminder must not fetch activity"))
    owner = ExecutionCapability()
    toolset, ctx = tools(operator)
    ctx.capabilities["a13n.subagents"] = toolset._subagents
    ctx.deps._tool_executions = SimpleNamespace(result_origins={})

    async def handler(request):
        return ModelContextProjection()

    request = ModelContextProjectionRequest(kind=ModelContextRequestKind.INPUT)
    projection = await owner.wrap_model_context(ctx, request, handler)
    text = projection.blocks[0].content
    assert text.startswith("Background work (20+)") and text.count(" | worker | running") == 20
    for _, confirm in request._notices.confirmations:
        confirm()
    assert not (
        await owner.wrap_model_context(ctx, ModelContextProjectionRequest(kind=ModelContextRequestKind.INPUT), handler)
    ).blocks
    operator.info.assert_not_awaited()
