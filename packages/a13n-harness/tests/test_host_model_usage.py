"""Host-owned review accounting without a conversational context or ledger."""

import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from a13n_harness.capabilities.tool_review import (
    AgentToolReviewer,
    ToolReviewConfig,
    ToolReviewError,
    ToolReviewRequest,
)
from a13n_harness.metering import HostModelUsage
from a13n_harness.model_calls import ModelCallCheckError
from a13n_harness.pricing import NoModelCostCapability
from a13n_harness.request_budget import RequestBudget
from a13n_harness.usage import UsageReportError
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import RequestUsage

pytestmark = pytest.mark.anyio


def request(call="appop_one"):
    return ToolReviewRequest(
        tool_id="mcp.fixture.write", tool_call_id=call, tool_name="write", parameters_schema={}, arguments={}
    )


@pytest.mark.parametrize(
    "outcome", ["success", "invalid", "failure", "report_failure", "report_cancel", "cancel", "timeout"]
)
@pytest.mark.parametrize("reported", ["tokens", "cost_only", "unknown"])
async def test_host_review_retains_facts_on_every_exit(monkeypatch, outcome, reported):
    checks = []
    reports = []
    entered = asyncio.Event()
    budget = RequestBudget(limit=1)

    def forbidden(*args, **kwargs):
        raise AssertionError("A Host operation must not fabricate a Run or Agent context")

    monkeypatch.setattr("a13n_harness.metering.RunUsageLedger", forbidden)
    monkeypatch.setattr("a13n_harness.context.AgentContext", forbidden)
    if outcome == "success":
        # Normal Host delivery has no Harness-owned deadline or cancellation shield.
        monkeypatch.setattr("a13n_harness.metering.anyio.move_on_after", forbidden)

    class Check:
        async def check(self, call):
            checks.append(call)
            budget.reserve(call.call_id)
            return budget

    async def report(record):
        reports.append(record)
        assert budget.pending == 0 and budget.used == 1
        if outcome == "report_failure":
            raise RuntimeError("report unavailable")
        if outcome == "report_cancel":
            raise asyncio.CancelledError()

    deadline = None
    original_timeout = asyncio.timeout

    def capture_timeout(seconds):
        nonlocal deadline
        scope = original_timeout(seconds)
        if deadline is None:
            deadline = scope
        return scope

    if outcome == "timeout":
        monkeypatch.setattr(asyncio, "timeout", capture_timeout)

    async def provider(messages, info):
        entered.set()
        yield {0: DeltaToolCall(name=info.output_tools[0].name, json_args='{"risk":')}
        if outcome in {"cancel", "timeout"}:
            if outcome == "timeout":
                deadline.reschedule(asyncio.get_running_loop().time())
            await asyncio.Event().wait()
        if outcome == "failure":
            raise RuntimeError("provider failed")
        yield {0: DeltaToolCall(json_args='"invalid"}' if outcome == "invalid" else '"low"}')}

    class Model(FunctionModel):
        @asynccontextmanager
        async def request_stream(self, *args, **kwargs):
            async with super().request_stream(*args, **kwargs) as response:
                try:
                    yield response
                finally:
                    response._usage = RequestUsage(
                        input_tokens=10 if reported == "tokens" else 0,
                        cost=None if reported == "unknown" else Decimal("0.125"),
                    )

    usage = HostModelUsage(
        check=Check(),
        cost=NoModelCostCapability(),
        source="tool.review",
        tool_id=request().tool_id,
        tool_call_id=request().tool_call_id,
        report=report,
    )
    reviewer = AgentToolReviewer(Model(stream_function=provider), ToolReviewConfig(model="review-model"))
    task = asyncio.create_task(reviewer.review_for_host(request(), usage=usage))
    if outcome == "cancel":
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
    if outcome in {"success"}:
        result = await task
        assert result.assessment.risk == "low" and result.usage == ()
    elif outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await task
    elif outcome in {"report_failure", "report_cancel"}:
        with pytest.raises(UsageReportError):
            await task
    else:
        with pytest.raises(ToolReviewError) as error:
            await task
        assert error.value.code == ("tool_review_timeout" if outcome == "timeout" else "tool_review_failed")
        assert error.value.usage == ()
    assert len(checks) == len(usage.records) == len(reports) == 1
    call, record = checks[0], usage.records[0]
    assert call.harness_run_id is None and call.agent_instance_id is None
    assert call.model_run_id and record.model_run_id == call.model_run_id
    assert record.call_id == call.call_id == usage.receipts()[0].usage_id
    assert record.model_id == call.model_id == "review-model"
    assert "run_id" not in record.model_dump() and "agent_instance_id" not in record.model_dump()
    assert record.request_usage.cost == (None if reported == "unknown" else Decimal("0.125"))
    assert usage.receipts()[0].cost == record.request_usage.cost
    assert budget.used == 1 and budget.pending == usage.requests.pending == 0


@pytest.mark.parametrize("error", [PermissionError(), asyncio.CancelledError()])
async def test_host_refusal_creates_no_usage(error):
    class Check:
        async def check(self, call):
            raise error

    async def provider(messages, info):
        pytest.fail("refused admission must not dispatch")
        yield "unreachable"

    usage = HostModelUsage(
        check=Check(),
        cost=NoModelCostCapability(),
        source="tool.review",
        tool_id=request().tool_id,
        tool_call_id=request().tool_call_id,
    )
    reviewer = AgentToolReviewer(FunctionModel(stream_function=provider), ToolReviewConfig(model="review-model"))
    with pytest.raises(ModelCallCheckError):
        await reviewer.review_for_host(request(), usage=usage)
    assert usage.records == () and usage.requests.used == 0


async def test_concurrent_host_reviews_have_independent_accounting():
    calls = []
    both = asyncio.Event()

    class Check:
        async def check(self, call):
            calls.append(call)

    async def provider(messages, info):
        if len(calls) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 5)
        yield {0: DeltaToolCall(name=info.output_tools[0].name, json_args='{"risk":"low"}')}

    reviewer = AgentToolReviewer(FunctionModel(stream_function=provider), ToolReviewConfig(model="review-model"))
    requests = [request("appop_one"), request("appop_two")]
    collectors = [
        HostModelUsage(
            check=Check(),
            cost=NoModelCostCapability(),
            source="tool.review",
            tool_id=item.tool_id,
            tool_call_id=item.tool_call_id,
        )
        for item in requests
    ]
    await asyncio.gather(
        *(reviewer.review_for_host(item, usage=usage) for item, usage in zip(requests, collectors, strict=True))
    )
    assert len({usage.records[0].call_id for usage in collectors}) == 2
    assert [usage.records[0].tool_call_id for usage in collectors] == [item.tool_call_id for item in requests]


async def test_host_report_cancellation_retries_captured_fact_without_model_replay():
    entered = asyncio.Event()
    reports = []
    dispatches = 0

    class Check:
        async def check(self, call):
            return None

    async def report(record):
        reports.append(record)
        if len(reports) == 1:
            entered.set()
            await asyncio.Event().wait()

    async def provider(messages, info):
        nonlocal dispatches
        dispatches += 1
        yield {0: DeltaToolCall(name=info.output_tools[0].name, json_args='{"risk":"low"}')}

    usage = HostModelUsage(
        check=Check(),
        cost=NoModelCostCapability(),
        source="tool.review",
        tool_id=request().tool_id,
        tool_call_id=request().tool_call_id,
        report=report,
    )
    reviewer = AgentToolReviewer(FunctionModel(stream_function=provider), ToolReviewConfig(model="review-model"))
    task = asyncio.create_task(reviewer.review_for_host(request(), usage=usage))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert dispatches == 1
    assert len(usage.records) == 1 and len(reports) == 2
    assert reports[0] == reports[1] == usage.records[0]
    assert usage.requests.used == 1 and usage.requests.pending == 0
