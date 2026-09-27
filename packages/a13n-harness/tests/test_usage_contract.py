"""Observable accounting, delivery and precision guarantees at the public Model boundary."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, localcontext

import pytest
from a13n_harness import HarnessBuilder, RunBindings
from a13n_harness.metering import ModelUsageBinding, ModelUsageCapability
from a13n_harness.money import sum_decimal
from a13n_harness.providers.usage import ProviderUsage
from a13n_harness.usage import (
    BoundedRequestUsage,
    ModelUsageRecord,
    ProviderUsageRecord,
    RunUsageLedger,
    UsageReportError,
    summarize_usage,
)
from pydantic import ValidationError
from pydantic_ai import Agent
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage, UsageLimits

pytestmark = pytest.mark.anyio
_NOW = datetime(2026, 9, 26, tzinfo=UTC)


def record(**fields) -> ModelUsageRecord:
    return ModelUsageRecord(
        record_id="usage-model",
        run_id="run-1",
        response_ordinal=0,
        agent_instance_id="agent-1",
        response_state="complete",
        response_timestamp=_NOW,
        request_started_at=_NOW,
        **fields,
    )


def test_current_contributions_cost_coverage_and_decimal_json() -> None:
    first = record(request_usage=BoundedRequestUsage(input_tokens=100, cache_read_tokens=80, cost="0.1"))
    second = first.model_copy(
        update={
            "request_usage": BoundedRequestUsage(input_tokens=200, cache_read_tokens=100, cost="0.2"),
        }
    )
    provider = ProviderUsageRecord(
        record_id="usage-provider",
        run_id="run-1",
        ordinal=1,
        agent_instance_id="agent-1",
        source="search",
        usage=ProviderUsage(
            usage_id="search-1", provider="search", product="web", timestamp=_NOW, cost="0.1", currency="USD"
        ),
    )
    unknown = first.model_copy(update={"record_id": "unknown", "request_usage": BoundedRequestUsage()})
    summary = summarize_usage((second, second, provider, unknown))
    assert summary.requests == 2 and summary.provider_receipts == 1
    assert summary.input_tokens == 200 and summary.cache_hit_rate == 0.5
    assert summary.cost == Decimal("0.3") and summary.unknown_cost_records == 1
    assert summary.model_dump(mode="json")["cost"] == "0.3"
    assert summarize_usage((unknown,)).cost is None
    assert summarize_usage((first.model_copy(update={"request_usage": BoundedRequestUsage(cost="0")}),)).cost == 0
    with pytest.raises(Exception, match="Conflicting"):
        summarize_usage((first, first.model_copy(update={"request_usage": BoundedRequestUsage(cost="9")})))
    with localcontext() as context:
        context.prec = 2
        assert sum_decimal((Decimal("123456789.0000000001"), Decimal("0.0000000002"))) == Decimal(
            "123456789.0000000003"
        )


def test_provider_currency_remains_explicit() -> None:
    values = dict(usage_id="1", provider="p", product="q", timestamp=_NOW)
    assert ProviderUsage(**values, cost="0", currency="eur").currency == "EUR"
    for value in ("NaN", "Infinity", "-1"):
        with pytest.raises(ValidationError):
            ProviderUsage(**values, cost=value, currency="USD")
    with pytest.raises(ValidationError):
        ProviderUsage(**values, cost="1")


async def test_background_polls_replace_one_generation_and_survive_replay() -> None:
    replies = [
        ModelResponse(
            parts=[TextPart(str(n))],
            state="suspended" if n < 3 else "complete",
            provider_response_id="job-one",
            usage=RequestUsage(input_tokens=10 * n, cost=Decimal(n) / 10),
        )
        for n in (1, 2, 3)
    ]
    binding = ModelUsageBinding.standalone(source="media")
    calls = []

    def model(messages, info):
        calls.append(messages)
        return replies[len(calls) - 1]

    binding.ledger._limits = UsageLimits(request_limit=1)
    result = await Agent(FunctionModel(model), capabilities=[ModelUsageCapability(binding)]).run("go")
    assert result.output == "3"
    (fact,) = binding.ledger.records
    assert fact.request_usage.input_tokens == 30
    assert binding.ledger.snapshot.sequence == 3
    assert binding.ledger.summary().requests == 1
    # Simulate checkpoint serialization and a new embedded execution.
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    history = ModelMessagesTypeAdapter.validate_json(
        ModelMessagesTypeAdapter.dump_json(
            [
                ModelRequest(parts=[UserPromptPart("go")]),
                replies[1],
            ]
        )
    )
    resumed = ModelUsageBinding.standalone(source="media")
    from dataclasses import replace

    resumed = replace(
        resumed,
        ledger=RunUsageLedger(
            run_id="run-new",
            instance=resumed.ledger.instance,
            snapshot=binding.ledger.snapshot,
        ),
    )

    class Reports:
        def __init__(self):
            self.records = []

        async def report(self, values):
            self.records.extend(values.records)

    reporter = Reports()
    resumed.ledger.reporter = reporter
    await Agent(FunctionModel(lambda messages, info: replies[2]), capabilities=[ModelUsageCapability(resumed)]).run(
        message_history=history
    )
    assert resumed.ledger.summary().requests == 1
    assert resumed.ledger.summary().input_tokens == 30
    assert resumed.ledger.records[0].record_id == fact.record_id
    assert reporter.records[0].record_id == fact.record_id


async def test_auxiliary_concurrency_reserves_request_budget_before_dispatch() -> None:
    binding = ModelUsageBinding.standalone(source="media")
    binding.ledger._limits = UsageLimits(request_limit=1)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def model(messages, info):
        entered.set()
        await finish.wait()
        return ModelResponse(parts=[TextPart("done")], usage=RequestUsage(input_tokens=3))

    agent = Agent(FunctionModel(model), capabilities=[ModelUsageCapability(binding)])
    first = asyncio.create_task(agent.run("first"))
    await entered.wait()
    try:
        with pytest.raises(UsageLimitExceeded):
            await agent.run("second")
    finally:
        finish.set()
        await first
    assert binding.ledger.summary().requests == 1


async def test_failed_reporter_preserves_usage_without_retrying_model() -> None:
    calls = []

    async def model(messages, info):
        calls.append(1)
        yield "done"

    class Broken:
        async def report(self, records):
            raise ConnectionError("delivery unavailable")

    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=model))
    async with executable.stream("go", bindings=RunBindings.embedded(usage_reporter=Broken())) as run:
        with pytest.raises(UsageReportError):
            async for _ in run:
                pass
        assert run.usage.requests == 1 and len(run.usage_records) == 1
    assert len(calls) == 1


def _delivery_ledger():
    ledger = ModelUsageBinding.standalone(source="test").ledger
    ledger._append(
        record(request_usage=BoundedRequestUsage(input_tokens=3)).model_copy(
            update={"run_id": ledger.run_id, "agent_instance_id": ledger.instance.agent_instance_id}
        )
    )
    return ledger


async def test_normal_delivery_has_no_harness_deadline_or_shield(monkeypatch):
    import anyio
    from a13n_harness import usage as usage_module

    delivered = []

    def unexpected_deadline(*args, **kwargs):
        pytest.fail("Normal usage delivery must use the Host's deadline policy")

    class Reporter:
        async def report(self, snapshot):
            assert anyio.current_effective_deadline() == float("inf")
            await anyio.lowlevel.checkpoint()
            delivered.append("persist")

    class Events:
        async def emit(self, event):
            assert anyio.current_effective_deadline() == float("inf")
            await anyio.lowlevel.checkpoint()
            delivered.append("display")

    monkeypatch.setattr(usage_module.anyio, "move_on_after", unexpected_deadline)
    ledger = _delivery_ledger()
    ledger.reporter, ledger._events = Reporter(), Events()
    await ledger._flush(reason="model_request")
    await ledger._flush(reason="terminal")
    assert delivered == ["persist", "display"]
    assert not ledger._pending


@pytest.mark.parametrize("failure", [TimeoutError("Host deadline"), asyncio.CancelledError("borrowed reporter")])
async def test_host_failure_retains_unacknowledged_snapshot(failure):
    class Reporter:
        async def report(self, snapshot):
            raise failure

    ledger = _delivery_ledger()
    ledger.reporter = Reporter()
    with pytest.raises(UsageReportError) as caught:
        await ledger._flush(reason="model_request")
    assert caught.value.__cause__ is failure
    assert len(ledger._pending) == 1
    assert ledger._persisted_sequence < ledger.snapshot.sequence


@pytest.mark.parametrize("stage", ["persist", "display"])
@pytest.mark.parametrize("cancel_kind", ["task", "scope"])
async def test_cancelled_delivery_persists_without_waiting_for_display(stage, cancel_kind):
    import anyio

    entered = asyncio.Event()
    reports = []
    displays = []
    scopes = []
    cancelled = []

    class Reporter:
        async def report(self, snapshot):
            reports.append(snapshot)
            if stage == "persist" and len(reports) == 1:
                entered.set()
                await anyio.sleep_forever()

    class Events:
        async def emit(self, event):
            displays.append(event)
            entered.set()
            await anyio.sleep_forever()

    ledger = _delivery_ledger()
    ledger.reporter, ledger._events = Reporter(), Events()

    async def deliver():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            try:
                await ledger._flush(reason="model_request")
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

    task = asyncio.create_task(deliver())
    await entered.wait()
    if cancel_kind == "task":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        scopes[0].cancel()
        await task
    assert cancelled == [True]
    assert len(reports) == (2 if stage == "persist" else 1)
    assert reports[0] == reports[-1]
    assert len(displays) == (1 if stage == "display" else 0)
    assert ledger._persisted_sequence == ledger.snapshot.sequence
    assert len(ledger._pending) == 1  # Display is not acknowledged by persistence cleanup.


async def test_cancelled_delivery_cleanup_is_bounded_and_retains_facts(monkeypatch, caplog):
    import anyio
    from a13n_harness import usage as usage_module

    entered = asyncio.Event()

    class Reporter:
        async def report(self, snapshot):
            entered.set()
            await anyio.sleep_forever()

    monkeypatch.setattr(usage_module, "_USAGE_CLEANUP_SECONDS", 0.01)
    ledger = _delivery_ledger()
    ledger.reporter = Reporter()
    task = asyncio.create_task(ledger._flush(reason="model_request"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ledger._persisted_sequence < ledger.snapshot.sequence
    assert len(ledger.records) == len(ledger._pending) == 1
    assert "Usage cleanup timed out" in caplog.text


async def test_display_retry_does_not_repeat_acknowledged_persistence():
    reports, displayed = [], []

    class Reporter:
        async def report(self, snapshot):
            reports.append(snapshot)

    class Events:
        async def emit(self, event):
            displayed.append(event)
            if len(displayed) == 1:
                raise ConnectionError("display unavailable")

    ledger = _delivery_ledger()
    ledger.reporter, ledger._events = Reporter(), Events()
    with pytest.raises(ConnectionError):
        await ledger._flush(reason="model_request")
    assert len(ledger._pending) == 1
    await ledger._flush(reason="terminal")
    assert len(reports) == 1 and len(displayed) == 2
    assert not ledger._pending


async def test_model_cancellation_uses_bounded_persistence_cleanup(monkeypatch, caplog):
    import anyio
    from a13n_harness import usage as usage_module

    entered = asyncio.Event()
    reports = []

    async def model(messages, info):
        entered.set()
        await anyio.sleep_forever()

    class Reporter:
        async def report(self, snapshot):
            reports.append(snapshot)
            await anyio.sleep_forever()

    monkeypatch.setattr(usage_module, "_USAGE_CLEANUP_SECONDS", 0.01)
    binding = ModelUsageBinding.standalone(source="test")
    binding.ledger.reporter = Reporter()
    agent = Agent(FunctionModel(model), capabilities=[ModelUsageCapability(binding)])
    task = asyncio.create_task(agent.run("go"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(reports) == len(binding.ledger.records) == 1
    assert not binding.ledger.requests.pending
    assert "Usage cleanup timed out" in caplog.text


@pytest.mark.parametrize("limits", [UsageLimits(total_tokens_limit=15), UsageLimits(cost_limit=Decimal("0.15"))])
async def test_auxiliary_cumulative_token_and_cost_limits_preserve_the_exceeded_call(limits) -> None:
    binding = ModelUsageBinding.standalone(source="media")
    binding.ledger._limits = limits
    calls = []

    def model(messages, info):
        calls.append(1)
        return ModelResponse(parts=[TextPart("done")], usage=RequestUsage(input_tokens=10, cost=Decimal("0.1")))

    agent = Agent(FunctionModel(model), capabilities=[ModelUsageCapability(binding)])
    await agent.run("first")
    with pytest.raises(UsageLimitExceeded):
        await agent.run("second")
    summary = binding.ledger.summary()
    assert summary.requests == 2 and summary.input_tokens == 20 and summary.cost == Decimal("0.2")
    with pytest.raises(UsageLimitExceeded):
        await agent.run("third")
    assert len(calls) == 2


async def test_unknown_background_usage_keeps_the_original_price_policy_on_resume() -> None:
    from dataclasses import replace

    from a13n_harness.pricing import AbstractModelCostCapability, ModelCostQuote

    class Cost(AbstractModelCostCapability):
        def __init__(self, revision):
            self._revision = revision

        @property
        def revision(self):
            return self._revision

        def quote(self, value):
            assert value.usage.has_values()
            return ModelCostQuote(cost_usd="99", source="custom", pricing_revision=self.revision)

    pending = ModelResponse(parts=[], state="suspended", provider_response_id="job-one")
    calls = []

    class BackgroundModel(FunctionModel):
        async def request(self, *args, **kwargs):
            response = await super().request(*args, **kwargs)
            # FunctionModel otherwise estimates tokens for empty usage responses.
            response.usage = RequestUsage()
            return response

    def before_restart(messages, info):
        calls.append(1)
        if len(calls) == 1:
            return pending
        raise RuntimeError("poll interrupted")

    first = replace(ModelUsageBinding.standalone(source="media"), cost=Cost("v1"))
    with pytest.raises(RuntimeError, match="poll interrupted"):
        await Agent(BackgroundModel(before_restart), capabilities=[ModelUsageCapability(first)]).run("go")
    (original,) = first.ledger.records
    assert original.pricing_revision == "v1" and original.request_usage.cost is None

    final = ModelResponse(parts=[TextPart("done")], provider_response_id="job-one", usage=RequestUsage(input_tokens=10))
    received = []

    class Reporter:
        async def report(self, records):
            received.extend(records.records)

    resumed = replace(ModelUsageBinding.standalone(source="media"), cost=Cost("v2"))
    resumed = replace(
        resumed,
        ledger=RunUsageLedger(
            run_id="run-new",
            instance=resumed.ledger.instance,
            snapshot=first.ledger.snapshot,
        ),
    )
    resumed.ledger.reporter = Reporter()
    await Agent(FunctionModel(lambda messages, info: final), capabilities=[ModelUsageCapability(resumed)]).run(
        message_history=[ModelRequest(parts=[UserPromptPart("go")]), pending]
    )
    (fact,) = received
    assert fact.record_id == original.record_id
    assert resumed.ledger.snapshot.sequence == 2
    assert fact.pricing_revision == "v1" and fact.request_usage.cost is None
    assert fact.request_usage.input_tokens == 10 and resumed.ledger.records == (fact,)


async def test_record_capacity_is_checked_before_another_model_is_dispatched(monkeypatch) -> None:
    monkeypatch.setattr("a13n_harness.usage._MAX_RECORDS", 1)
    binding = ModelUsageBinding.standalone(source="media")
    calls = []

    def model(messages, info):
        calls.append(1)
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(FunctionModel(model), capabilities=[ModelUsageCapability(binding)])
    await agent.run("one")
    with pytest.raises(Exception, match="capacity"):
        await agent.run("two")
    assert len(calls) == 1 and binding.ledger.summary().requests == 1


async def test_finished_request_releases_capacity_before_reporting() -> None:
    binding = ModelUsageBinding.standalone(source="media")
    binding.ledger._limits = UsageLimits(request_limit=2)
    reporting, release, second_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    class Reporter:
        async def report(self, records):
            reporting.set()
            await release.wait()

    def model(messages, info):
        calls.append(1)
        if len(calls) == 2:
            second_entered.set()
        return ModelResponse(parts=[TextPart("done")], usage=RequestUsage(input_tokens=3))

    binding.ledger.reporter = Reporter()
    agent = Agent(FunctionModel(model), capabilities=[ModelUsageCapability(binding)])
    tasks = [asyncio.create_task(agent.run("first"))]
    await reporting.wait()
    tasks.append(asyncio.create_task(agent.run("second")))
    try:
        await asyncio.wait_for(second_entered.wait(), 1)
        with pytest.raises(UsageLimitExceeded):
            await agent.run("third")
    finally:
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    assert all(not isinstance(result, BaseException) for result in results)
    assert binding.ledger.summary().requests == 2
    assert len(calls) == 2


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "call_id",
        "model_id",
        "model_run_id",
        "provider_response_id",
        "agent_instance_id",
        "parent_agent_instance_id",
        "delegation_id",
        "source",
        "tool_id",
        "tool_call_id",
        "request_started_at",
        "response_ordinal",
    ],
)
def test_observations_cannot_change_generation_ownership(field) -> None:
    from a13n_harness.errors import RunError
    from a13n_harness.usage import validate_contribution

    original = record(request_usage=BoundedRequestUsage(input_tokens=1))
    changed = original.model_copy(
        update={
            field: _NOW.replace(year=2027)
            if field == "request_started_at"
            else 1
            if field == "response_ordinal"
            else "different",
        }
    )
    for before, after in ((original, changed), (changed, original)):
        with pytest.raises(RunError, match="attribution"):
            validate_contribution(before, after)


def test_update_refines_only_observation_and_preserves_policy() -> None:
    from a13n_harness.usage import ModelUsageObservation, update_observation, validate_contribution

    original = record(
        call_id="call-first",
        request_usage=BoundedRequestUsage(input_tokens=1, cost="0.1"),
        pricing_revision="v1",
        pricing_rule_id="rule1",
    )
    observation = ModelUsageObservation(
        response_state="complete",
        response_timestamp=_NOW,
        request_usage=BoundedRequestUsage(input_tokens=10, cost="0.2"),
        pricing_revision="v2",
        pricing_rule_id="rule2",
        pricing_status="applied",
    )
    revised = update_observation(original, observation)
    assert revised.call_id == original.call_id
    assert revised.pricing_revision == "v1" and revised.pricing_rule_id == "rule1"
    assert revised.pricing_status == "declined" and revised.request_usage.cost is None
    assert revised.request_usage.input_tokens == 10
    validate_contribution(original, revised)
    with pytest.raises(Exception, match="price policy"):
        validate_contribution(
            original, revised.model_copy(update={"pricing_revision": "v2", "request_usage": observation.request_usage})
        )


async def test_failed_report_retries_facts_without_holding_request_capacity() -> None:
    binding = ModelUsageBinding.standalone(source="media")
    binding.ledger._limits = UsageLimits(request_limit=2)
    received = []

    class Reporter:
        fail = True

        async def report(self, records):
            if self.fail:
                self.fail = False
                raise ConnectionError("retry delivery")
            received.extend(records.records)

    binding.ledger.reporter = Reporter()
    agent = Agent(
        FunctionModel(
            lambda messages, info: ModelResponse(parts=[TextPart("done")], usage=RequestUsage(input_tokens=3))
        ),
        capabilities=[ModelUsageCapability(binding)],
    )
    with pytest.raises(UsageReportError):
        await agent.run("first")
    await agent.run("second")
    assert binding.ledger.summary().requests == 2
    assert len(received) == 2 and len({item.record_id for item in received}) == 2
    assert binding.ledger.requests.pending == 0


async def test_cancelled_harness_terminal_does_not_restart_unbounded_delivery(monkeypatch):
    import anyio
    from a13n_harness import HarnessRunResultEvent
    from a13n_harness import usage as usage_module

    entered = asyncio.Event()
    reports = []

    async def model(messages, info):
        entered.set()
        await anyio.sleep_forever()
        yield "unreachable"

    class Reporter:
        async def report(self, snapshot):
            reports.append(snapshot)
            await anyio.sleep_forever()

    monkeypatch.setattr(usage_module, "_USAGE_CLEANUP_SECONDS", 0.01)
    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=model))
    async with executable.stream("go", bindings=RunBindings.embedded(usage_reporter=Reporter())) as stream:

        async def consume():
            return [item async for item in stream]

        task = asyncio.create_task(consume())
        await entered.wait()
        stream.cancel()
        items = await asyncio.wait_for(task, timeout=2)
    assert isinstance(items[-1], HarnessRunResultEvent)
    assert items[-1].result.status == "cancelled"
    assert items[-1].result.usage.requests == 1
    assert len(items[-1].result.usage_records) == 1
    assert reports


@pytest.mark.parametrize("first_report", ["timeout", "blocked"])
async def test_failed_harness_terminal_uses_bounded_cleanup(monkeypatch, caplog, first_report):
    import anyio
    from a13n_harness import HarnessRunResultEvent
    from a13n_harness import usage as usage_module

    reports = []
    calls = []

    async def model(messages, info):
        calls.append(True)
        raise RuntimeError("model failed")
        yield "unreachable"

    class Reporter:
        async def report(self, snapshot):
            reports.append(snapshot)
            if first_report == "timeout" and len(reports) == 1:
                raise TimeoutError("Host deadline")
            await anyio.sleep_forever()

    monkeypatch.setattr(usage_module, "_USAGE_CLEANUP_SECONDS", 0.01)
    executable = HarnessBuilder().build(AgentSpec(), output_type=str, model=FunctionModel(stream_function=model))
    async with executable.stream("go", bindings=RunBindings.embedded(usage_reporter=Reporter())) as stream:

        async def consume():
            return [item async for item in stream]

        items = await asyncio.wait_for(consume(), timeout=2)
    assert isinstance(items[-1], HarnessRunResultEvent)
    assert items[-1].result.status == "failed"
    assert items[-1].result.usage.requests == 1
    assert len(items[-1].result.usage_records) == 1
    assert calls == [True]
    assert len(reports) == 2
    assert reports[0] == reports[1]
    assert "Usage cleanup timed out" in caplog.text


async def test_cleanup_budget_does_not_reject_a_shielded_commit(monkeypatch, caplog):
    import anyio
    from a13n_harness import usage as usage_module

    scopes, reports = [], []

    def deadline(*args, **kwargs):
        scope = anyio.CancelScope(shield=True)
        scopes.append(scope)
        return scope

    class Reporter:
        async def report(self, snapshot):
            with anyio.CancelScope(shield=True):
                scopes[0].cancel()
                await anyio.lowlevel.checkpoint()
                reports.append(snapshot)

    monkeypatch.setattr(usage_module.anyio, "move_on_after", deadline)
    ledger = _delivery_ledger()
    ledger.reporter = Reporter()
    await ledger._flush_cleanup()
    assert len(reports) == 1
    assert ledger._persisted_sequence == ledger.snapshot.sequence
    assert "Usage cleanup timed out" not in caplog.text
