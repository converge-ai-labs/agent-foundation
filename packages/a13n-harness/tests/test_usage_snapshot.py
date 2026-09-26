"""Context-owned accounting continuation through the public Harness entry points."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from a13n_harness import HarnessBuilder, HarnessState, StateError
from a13n_harness.events import HarnessRunResultEvent
from a13n_harness.state import AgentContextStateSnapshot, CapabilityState
from a13n_harness.usage import USAGE_CAPABILITY_ID, UsageSnapshot, select_usage_snapshot
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage, RunUsage, UsageLimits

pytestmark = pytest.mark.anyio


def executable():
    async def chunks(messages, info):
        yield "done"

    class FixedUsageModel(FunctionModel):
        @asynccontextmanager
        async def request_stream(self, *args, **kwargs):
            async with super().request_stream(*args, **kwargs) as response:
                yield response
                response._usage = RequestUsage(input_tokens=10, output_tokens=2, cost=Decimal("0.1"))

    return HarnessBuilder().build(AgentSpec(), output_type=str, model=FixedUsageModel(stream_function=chunks))


async def consume(stream):
    async for _ in stream:
        pass
    assert stream.result is not None
    return stream.result


def snapshot(state: HarnessState | None) -> UsageSnapshot:
    assert state is not None
    value = UsageSnapshot.from_state(state)
    assert value is not None
    return value


async def test_default_resets_and_explicit_resume_preserves_accounting() -> None:
    agent = executable()
    first = await agent.run("first")
    assert first.output_or_raise() == "done"
    initial = snapshot(first.state)
    assert first.usage == initial.summary
    assert initial.sequence == 1 and initial.summary.requests == 1
    assert "revision" not in initial.records[0].model_dump()
    assert first.state is not None
    restored = HarnessState.model_validate_json(first.state.model_dump_json())

    ordinary = await agent.run("next", previous_state=restored)
    fresh = snapshot(ordinary.state)
    assert fresh.usage_id != initial.usage_id and fresh.summary.requests == 1

    continued = await agent.run("continue", previous_state=restored, resume_usage=True)
    resumed = snapshot(continued.state)
    assert continued.run_id != first.run_id
    assert resumed.usage_id == initial.usage_id and resumed.run_id == first.run_id
    assert resumed.sequence == 2 and resumed.summary.requests == 2
    assert resumed.summary.input_tokens == 20 and resumed.summary.cost == Decimal("0.2")
    assert resumed.records[0] == initial.records[0]
    assert first.state == restored


async def test_fork_and_old_checkpoint_use_the_same_fresh_path() -> None:
    agent = executable()
    first = await agent.run("first")
    assert first.state is not None
    forked = first.state.fork()
    fork_result = await agent.run("fork", previous_state=forked)
    assert snapshot(fork_result.state).usage_id != snapshot(first.state).usage_id
    assert fork_result.usage.requests == 1
    with pytest.raises(StateError, match="another Thread"):
        agent.stream("bad", previous_state=forked, resume_usage=True)

    legacy = HarnessState.new(message_history=first.state.message_history)
    result = await agent.run("old state", previous_state=legacy)
    assert result.usage.requests == 1
    with pytest.raises(StateError, match="requires usage state"):
        agent.stream(previous_state=legacy, resume_usage=True)
    with pytest.raises(StateError, match="requires usage state"):
        await agent.run("bad", resume_usage=True)


async def test_latest_accounting_overlays_an_older_execution_checkpoint() -> None:
    agent = executable()
    first = await agent.run("first")
    assert first.state is not None
    second = await agent.run("second", previous_state=first.state, resume_usage=True)
    latest = snapshot(second.state)
    checkpoint = latest.restore(first.state)
    assert checkpoint.message_history == first.state.message_history
    assert snapshot(checkpoint) == latest
    resumed = await agent.run("third", previous_state=checkpoint, resume_usage=True)
    assert resumed.usage.requests == 3
    assert snapshot(resumed.state).records[:2] == latest.records
    assert select_usage_snapshot(latest, snapshot(first.state)) == latest
    assert select_usage_snapshot(latest, deepcopy(latest)) == latest
    with pytest.raises(StateError, match="conflicting"):
        select_usage_snapshot(latest, latest.model_copy(update={"tool_calls": 1}))
    with pytest.raises(StateError, match="lost an observed"):
        select_usage_snapshot(latest, latest.model_copy(update={"sequence": latest.sequence + 1, "records": ()}))


async def test_snapshot_and_context_namespace_are_detached() -> None:
    agent = executable()
    async with agent.stream("first") as stream:
        async for item in stream:
            if isinstance(item, HarnessRunResultEvent):
                first = stream.context.usage_snapshot
                first.records[0].request_usage.details["mutated"] = 99
                assert "mutated" not in stream.context.usage_snapshot.records[0].request_usage.details
                exported = await stream.export_state()
                assert snapshot(exported) == stream.context.usage_snapshot
                entry = await stream.context.state.read(USAGE_CAPABILITY_ID, UsageSnapshot, version="1")
                assert entry == stream.context.usage_snapshot


async def test_restored_requests_count_toward_limits_but_not_external_baseline_twice() -> None:
    agent = executable()
    first = await agent.run("first")
    async with agent.stream(
        "second", previous_state=first.state, resume_usage=True, usage_limits=UsageLimits(request_limit=1)
    ) as stream:
        result = await consume(stream)
    assert result.status == "failed" and result.usage.requests == 1
    # The native argument is a Host budget baseline, not part of the current scope.
    fresh = await agent.run("fresh", usage=RunUsage(requests=5), usage_limits=UsageLimits(request_limit=6))
    assert fresh.usage.requests == 1


async def test_provider_receipts_are_restored_without_recharging() -> None:
    from a13n_harness.providers.usage import ProviderUsage

    agent = executable()
    receipt = ProviderUsage(
        usage_id="one", provider="p", product="q", timestamp=datetime.now(UTC), cost=Decimal("0.01"), currency="USD"
    )
    async with agent.stream("first") as stream:
        await stream.context.record_provider_usage(receipt, source="test")
        first = await consume(stream)
    async with agent.stream("next", previous_state=first.state, resume_usage=True) as stream:
        await stream.context.record_provider_usage(receipt, source="test")
        result = await consume(stream)
    assert result.usage.provider_receipts == 1
    assert result.usage.requests == 2
    assert result.usage.cost == Decimal("0.21")


async def test_fresh_run_replaces_an_unreadable_old_usage_namespace() -> None:
    state = HarnessState.new(
        agent_context_state=AgentContextStateSnapshot(
            entries={
                USAGE_CAPABILITY_ID: CapabilityState(version="unsupported", data={"old": True}),
            }
        )
    )
    result = await executable().run("fresh", previous_state=state)
    assert snapshot(result.state).summary.requests == 1
    with pytest.raises(StateError, match="Unsupported"):
        executable().stream("resume", previous_state=state, resume_usage=True)


@pytest.mark.parametrize("baseline,limit,allowed", [(0, 1, False), (3, 4, False), (3, 5, True)])
async def test_resumed_native_tool_budget_includes_saved_scope_only_once(baseline, limit, allowed):
    from a13n_harness import RunBindings
    from a13n_harness.tools import HarnessTool, InvocationPolicyCapability
    from pydantic_ai.capabilities import Capability
    from pydantic_ai.models.function import DeltaToolCall

    from .test_usage import _allow, _metadata

    executed = []

    async def metered_search():
        executed.append(True)
        return "ok"

    request = 0

    async def chunks(messages, info):
        nonlocal request
        request += 1
        if request % 2:
            yield {0: DeltaToolCall(name="metered_search", json_args="{}", tool_call_id=f"tool-{request}")}
        else:
            yield "done"

    agent = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=chunks),
        capabilities=(Capability(tools=[HarnessTool(metered_search, harness_metadata=_metadata())], id="tools"),),
    )
    bindings = RunBindings.embedded(capabilities=(InvocationPolicyCapability(evaluator=_allow),))
    first = await agent.run("first", bindings=bindings)
    assert first.output_or_raise() == "done"
    assert snapshot(first.state).tool_calls == first.usage.tool_calls == 1
    result = await agent.run(
        "second",
        previous_state=first.state,
        resume_usage=True,
        bindings=bindings,
        usage=RunUsage(tool_calls=baseline),
        usage_limits=UsageLimits(tool_calls_limit=limit),
    )
    assert len(executed) == (2 if allowed else 1)
    assert result.status == ("completed" if allowed else "failed")
    assert result.usage.tool_calls == (2 if allowed else 1)
    assert snapshot(result.state).summary == result.usage


async def test_scope_cancellation_redelivery_does_not_interrupt_producer_cleanup():
    import asyncio

    import anyio

    ready, draining, cleaning, release, cleaned = (asyncio.Event() for _ in range(5))
    scopes = []

    async def producer():
        try:
            ready.set()
            await asyncio.Event().wait()
        finally:
            with anyio.CancelScope(shield=True):
                cleaning.set()
                await release.wait()
                cleaned.set()

    stream = executable().stream("unused")
    producer_task = asyncio.create_task(producer())
    stream._response_pump_task = producer_task
    await ready.wait()

    async def consumer():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            try:
                draining.set()
                await asyncio.Event().wait()
            finally:
                await stream._stop_response_pump()

    drain = asyncio.create_task(consumer())
    await draining.wait()
    scopes[0].cancel()
    await cleaning.wait()
    try:
        # Give the cancelled scope repeated opportunities to redeliver. Unlike a
        # new explicit Task.cancel(), this must not pierce producer cleanup.
        for _ in range(10):
            await asyncio.sleep(0)
        assert not producer_task.done()
        assert producer_task.cancelling() == 1
    finally:
        release.set()
        await asyncio.wait_for(drain, timeout=2)
    assert cleaned.is_set()


async def test_public_provider_continuation_uses_latest_scope_and_rejects_reset_before_dispatch():
    from a13n_harness import RunBindings
    from pydantic_ai.messages import ModelResponse, TextPart

    replies = iter([(10, "suspended"), (20, "complete"), (10, "suspended"), (30, "complete")])
    dispatched = []

    async def chunks(messages, info):
        yield "done"

    class BackgroundModel(FunctionModel):
        @asynccontextmanager
        async def request_stream(self, *args, **kwargs):
            tokens, state = next(replies)
            dispatched.append(tokens)
            async with super().request_stream(*args, **kwargs) as response:
                response.provider_response_id = "job-one"
                response.state = state
                yield response
                response._usage = RequestUsage(input_tokens=tokens, cost=Decimal(tokens) / 100)

    class Reports:
        def __init__(self):
            self.values = []

        async def report(self, value):
            self.values.append(value)

    reports = Reports()
    bindings = RunBindings.embedded(usage_reporter=reports)
    agent = HarnessBuilder().build(AgentSpec(), output_type=str, model=BackgroundModel(stream_function=chunks))
    first = await agent.run("go", bindings=bindings)
    assert first.output_or_raise() == "done"
    latest = snapshot(first.state)
    assert latest.summary.requests == 1 and latest.summary.input_tokens == 20
    assert first.state is not None
    checkpoint = HarnessState.new(
        thread_id=first.state.thread_id,
        agent_context_state=first.state.agent_context_state,
        message_history=(
            first.state.message_history[0],
            ModelResponse(
                parts=[TextPart("pending")],
                state="suspended",
                provider_response_id="job-one",
                metadata={"a13n_usage": {"usage_id": latest.usage_id, "record_id": latest.records[0].record_id}},
                usage=RequestUsage(input_tokens=10),
            ),
        ),
    )
    with pytest.raises(StateError) as refused:
        await agent.run(previous_state=checkpoint)
    assert refused.value.code == "usage_resume_required" and len(dispatched) == 2
    report_count = len(reports.values)
    resumed = await agent.run(previous_state=checkpoint, resume_usage=True, bindings=bindings)
    assert resumed.output_or_raise() == "done"
    assert resumed.usage.requests == 1 and resumed.usage.input_tokens == 30
    assert snapshot(resumed.state).records[0].record_id == latest.records[0].record_id
    assert all(value.summary.input_tokens >= 20 for value in reports.values[report_count:])
