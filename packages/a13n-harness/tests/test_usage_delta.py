"""Direct delta delivery acknowledges persistence independently of the display stream."""

import asyncio
from datetime import UTC, datetime

import pytest
from a13n_harness import HarnessBuilder, RunBindings
from a13n_harness.metering import ModelUsageBinding
from a13n_harness.usage import (
    BoundedRequestUsage,
    ModelUsageRecord,
    UsageDelta,
    UsageReportError,
    UsageSnapshot,
)
from pydantic import ValidationError
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

pytestmark = pytest.mark.anyio


def contribution(ledger, name="first", tokens=3):
    return ModelUsageRecord(
        record_id=name,
        run_id=ledger.run_id,
        agent_instance_id=ledger.instance.agent_instance_id,
        response_ordinal=0,
        response_state="complete",
        response_timestamp=datetime(2026, 9, 30, tzinfo=UTC),
        request_usage=BoundedRequestUsage(input_tokens=tokens),
    )


class Reports:
    def __init__(self):
        self.received: list[UsageDelta] = []

    async def report_delta(self, delta: UsageDelta) -> None:
        self.received.append(delta)


async def test_only_new_or_refined_contributions_are_delivered():
    ledger = ModelUsageBinding.standalone(source="test").ledger
    reports = Reports()
    ledger.reporter = reports
    first, second = contribution(ledger), contribution(ledger, "second")
    ledger._append(first)
    await ledger._flush(reason="model_request")
    ledger._append(second)
    await ledger._flush(reason="model_request")
    revised = contribution(ledger, tokens=7)
    ledger._append(revised)
    await ledger._flush(reason="model_request")
    await ledger._flush(reason="terminal")
    assert [delta.records for delta in reports.received] == [(first,), (second,), (revised,)]
    assert [(delta.after_sequence, delta.scope.sequence) for delta in reports.received] == [(0, 1), (1, 2), (2, 3)]
    assert ledger.snapshot.records == (revised, second)
    ledger.summary(tool_calls=2)
    await ledger._flush(reason="terminal")
    assert reports.received[-1].records == ()
    assert reports.received[-1].scope.tool_calls == 2


async def test_unacknowledged_changes_survive_failure_and_include_later_refinements():
    ledger = ModelUsageBinding.standalone(source="test").ledger
    reports = Reports()

    class Failing:
        async def report_delta(self, delta):
            await reports.report_delta(delta)
            raise OSError("unknown commit outcome")

    ledger.reporter = Failing()
    ledger._append(contribution(ledger))
    with pytest.raises(UsageReportError):
        await ledger._flush(reason="model_request")
    ledger.reporter = reports
    ledger._append(contribution(ledger, tokens=7))
    ledger._append(contribution(ledger, "second"))
    await ledger._flush(reason="model_request")
    retry = reports.received[-1]
    assert retry.after_sequence == 0 and retry.scope.sequence == 3
    assert {record.record_id for record in retry.records} == {"first", "second"}
    assert retry.records[0].request_usage.input_tokens == 7


async def test_display_failure_does_not_resend_acknowledged_contributions():
    ledger = ModelUsageBinding.standalone(source="test").ledger
    reports = Reports()

    class BrokenDisplay:
        async def emit(self, event):
            raise OSError("display unavailable")

    ledger.reporter, ledger._events = reports, BrokenDisplay()
    ledger._append(contribution(ledger))
    with pytest.raises(OSError):
        await ledger._flush(reason="model_request")
    assert len(ledger._pending) == 1
    ledger._events = None
    second = contribution(ledger, "second")
    ledger._append(second)
    await ledger._flush(reason="model_request")
    assert reports.received[-1].after_sequence == 1
    assert reports.received[-1].records == (second,)
    assert not ledger._pending


async def test_changes_during_delivery_are_left_for_the_next_batch():
    ledger = ModelUsageBinding.standalone(source="test").ledger
    reports = Reports()
    entered, release = asyncio.Event(), asyncio.Event()

    class Paused:
        async def report_delta(self, delta):
            await reports.report_delta(delta)
            entered.set()
            await release.wait()

    ledger.reporter = Paused()
    ledger._append(contribution(ledger))
    sending = asyncio.create_task(ledger._flush(reason="model_request"))
    await entered.wait()
    revised = contribution(ledger, tokens=7)
    ledger._append(revised)
    release.set()
    await sending
    await ledger._flush(reason="terminal")
    assert reports.received[0].records[0].request_usage.input_tokens == 3
    assert reports.received[1].records == (revised,)
    assert reports.received[1].after_sequence == 1


async def test_delta_takes_precedence_and_cannot_mutate_ledger_or_display():
    ledger = ModelUsageBinding.standalone(source="test").ledger
    displays = []

    class MutatingReporter:
        async def report(self, snapshot):
            pytest.fail("Delta delivery takes precedence over snapshots")

        async def report_delta(self, delta):
            delta.records[0].request_usage.details["injected"] = 42

    class Display:
        async def emit(self, event):
            displays.append(event)

    ledger.reporter, ledger._events = MutatingReporter(), Display()
    ledger._append(contribution(ledger))
    await ledger._flush(reason="model_request")
    assert ledger.snapshot.records[0].request_usage.details == {}
    assert displays[0].payload["records"][0]["request_usage"]["details"] == {}


async def test_cancellation_retries_the_unacknowledged_batch_without_display():
    ledger = ModelUsageBinding.standalone(source="test").ledger
    reports = Reports()
    entered = asyncio.Event()

    class Interrupted:
        async def report_delta(self, delta):
            await reports.report_delta(delta)
            if len(reports.received) == 1:
                entered.set()
                await asyncio.Event().wait()

    class NoDisplay:
        async def emit(self, event):
            pytest.fail("Cancellation cleanup must not depend on display")

    ledger.reporter, ledger._events = Interrupted(), NoDisplay()
    ledger._append(contribution(ledger))
    sending = asyncio.create_task(ledger._flush(reason="model_request"))
    await entered.wait()
    sending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sending
    assert len(reports.received) == 2 and reports.received[0] == reports.received[1]
    assert ledger._persisted_sequence == 1
    assert len(ledger._pending) == 1


async def test_delta_reporter_keeps_complete_checkpoint_and_explicit_resume():
    reports = Reports()
    agent = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart("done")])),
    )
    first = await agent.run("first", bindings=RunBindings.embedded(usage_reporter=reports))
    assert first.state is not None
    saved = UsageSnapshot.from_state(first.state)
    assert saved is not None and len(saved.records) == 1
    second = await agent.run(
        "second", previous_state=first.state, resume_usage=True, bindings=RunBindings.embedded(usage_reporter=reports)
    )
    assert second.state is not None
    restored = UsageSnapshot.from_state(second.state)
    assert restored is not None and len(restored.records) == 2
    assert reports.received[-1].after_sequence == saved.sequence
    assert reports.received[-1].records == restored.records[1:]


def test_delta_rejects_nonadvancing_progress_and_mismatched_contributions():
    ledger = ModelUsageBinding.standalone(source="test").ledger
    ledger._append(contribution(ledger))
    snapshot = ledger.snapshot
    with pytest.raises(ValidationError, match="advance"):
        UsageDelta(scope=snapshot.scope, after_sequence=snapshot.sequence, records=snapshot.records)
    with pytest.raises(ValidationError, match="one owner"):
        UsageDelta(
            scope=snapshot.scope,
            after_sequence=0,
            records=(snapshot.records[0].model_copy(update={"run_id": "other"}),),
        )
    with pytest.raises(ValidationError, match="unique"):
        UsageDelta(scope=snapshot.scope, after_sequence=0, records=(*snapshot.records, *snapshot.records))
