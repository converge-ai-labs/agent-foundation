"""Recovery admission with real relational fencing and conditional object storage."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness import HarnessState, SafeFailure
from a13n_service.interactions import state_admission
from a13n_service.interactions.attempt_executor import ControlWatcher, LeaseMonitor, RunAttemptExecutor
from a13n_service.interactions.attempts import AttemptAuthorityError, AttemptExecutionService
from a13n_service.interactions.inbox import DatabaseThreadInboxReconciler, ThreadInboxStore
from a13n_service.interactions.inbox_delivery import AdaptedThreadInboxEntry
from a13n_service.interactions.input import AcceptedAgentInput, TextContent
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.objects import RunObjectIntegrityError, RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.interactions.state import ConsumedThreadInboxEntry
from a13n_service.interactions.terminal_committer import DatabaseRunTerminalCommitter
from a13n_service.storage import ObjectAccessDenied, ObjectStoreUnavailable, short_session
from anyio import Event, create_task_group, fail_after, sleep_forever
from pydantic_ai.messages import ModelRequest, UserPromptPart
from sqlalchemy import select

from tests.lifecycle_support import test_lifecycle_writer

from . import test_attempt_execution as acceptance

pytestmark = pytest.mark.anyio


@pytest.fixture
async def admission(interaction_sessions, interaction_object_store, monkeypatch):
    states, run, _ = await acceptance._accept_root(interaction_sessions, interaction_object_store)
    execution = AttemptExecutionService(interaction_sessions, lifecycle=test_lifecycle_writer())
    scheduler = AttemptScheduler(interaction_sessions, lifecycle=test_lifecycle_writer())
    prior = await scheduler.claim(run.id, acceptance._worker())
    assert isinstance(prior, ClaimedAttempt)
    await execution.fail(
        acceptance._authority(prior), SafeFailure(code="timeout", message="Worker stopped."), retryable=True
    )
    claimed = await scheduler.claim(run.id, acceptance._worker(worker_id="replacement"))
    assert isinstance(claimed, ClaimedAttempt)
    context = replace(acceptance._authority(claimed), renewal_interval=timedelta(milliseconds=5))
    materialize = AsyncMock(side_effect=AssertionError("Preparation must not materialize new inbox input"))
    inbox = DatabaseThreadInboxReconciler(interaction_sessions, materialize)
    confirm = AsyncMock(wraps=inbox.confirm_checkpoint)
    monkeypatch.setattr(inbox, "confirm_checkpoint", confirm)
    control = RunAttemptControl(context=context, execution=execution, states=states, inbox=inbox)
    driver = Mock(cancel=AsyncMock(), run=AsyncMock(side_effect=AssertionError("Harness entry forbidden")))
    monkeypatch.setattr(state_admission, "CLAIM_BACKOFF_SECONDS", 0)
    return run, prior, states, execution, control, driver, confirm


class _Wakeups:
    async def receive(self):
        await sleep_forever()

    async def acknowledge(self, signal):
        raise AssertionError("No signal was emitted")


async def test_claim_conflict_recovers_the_complete_late_checkpoint(admission, monkeypatch):
    run, prior, states, _, control, driver, _ = admission
    initial = await states.read_run(run)
    old = await states.claim_writer(initial, fence=prior.attempt.fence)
    candidate = acceptance._completed_state(old.envelope, prior.attempt.id, prior.attempt.fence)
    claim = states.claim_writer
    calls = []

    async def race(state, *, fence):
        calls.append(state)
        if len(calls) == 1:
            await states.replace(old, candidate, run_attempt_id=prior.attempt.id, fence=prior.attempt.fence)
        return await claim(state, fence=fence)

    monkeypatch.setattr(states, "claim_writer", race)
    control.bind_executor(driver, lambda: None)
    await control.claim_state(run)

    assert len(calls) == 2
    assert calls[0].envelope.checkpoint_kind == "initial"
    assert calls[1].envelope == candidate
    assert control.current_state.envelope == candidate
    assert control.current_state.writer_fence == control.current_context.fence
    assert control.current_state.info.version != calls[1].info.version
    assert control.current_state.envelope.last_checkpoint_fence == prior.attempt.fence


async def test_lost_claim_response_is_reconciled_without_a_second_publication(admission, monkeypatch):
    run, _, states, _, control, driver, _ = admission
    claim = states.claim_writer
    put = states._objects.put
    writes = AsyncMock(wraps=put)
    monkeypatch.setattr(states._objects, "put", writes)
    calls = 0

    async def lose_response(state, *, fence):
        nonlocal calls
        calls += 1
        result = await claim(state, fence=fence)
        if calls == 1:
            raise TimeoutError("response lost after CAS commit")
        return result

    monkeypatch.setattr(states, "claim_writer", lose_response)
    control.bind_executor(driver, lambda: None)
    await control.claim_state(run)

    assert calls == 2
    assert writes.await_count == 1
    assert control.current_state == await states.read_run(run)
    assert control.current_state.envelope.checkpoint_seq == 0


@pytest.mark.parametrize("failure", ["higher_fence", "invalid_state"])
async def test_permanent_or_newer_authority_is_never_retried(admission, monkeypatch, failure):
    run, _, states, _, control, driver, _ = admission
    if failure == "higher_fence":
        await states.claim_writer(await states.read_run(run), fence=control.current_context.fence + 1)
        read = AsyncMock(wraps=states.read_run)
        expected = AttemptAuthorityError
    else:
        read = AsyncMock(side_effect=RunObjectIntegrityError("invalid digest"))
        expected = RunObjectIntegrityError
    monkeypatch.setattr(states, "read_run", read)
    write = AsyncMock(side_effect=AssertionError("Invalid state must not be claimed"))
    monkeypatch.setattr(states, "claim_writer", write)
    control.bind_executor(driver, lambda: None)
    with pytest.raises(expected):
        await control.claim_state(run)
    assert read.await_count == 1
    write.assert_not_awaited()


async def test_slow_initial_read_renews_without_confirming_provisional_inbox(admission, monkeypatch):
    run, _, states, execution, control, driver, confirm = admission
    renewed = Event()
    heartbeat = execution.heartbeat
    read = states.read_run

    async def renew(*args, **kwargs):
        receipt = await heartbeat(*args, **kwargs)
        renewed.set()
        return receipt

    async def blocked_read(*args):
        await renewed.wait()
        confirm.assert_not_awaited()
        return await read(*args)

    monkeypatch.setattr(execution, "heartbeat", renew)
    monkeypatch.setattr(states, "read_run", blocked_read)
    with fail_after(2):
        async with create_task_group() as tasks:
            control.bind_executor(driver, tasks.cancel_scope.cancel)
            await tasks.start(LeaseMonitor(control.current_context, control).run)
            await tasks.start(ControlWatcher(control.current_context, control, _Wakeups()).run)
            await control.claim_state(run)
            confirm.assert_not_awaited()
            await control.commit_preparation()
            await control.reconcile_recovery_state()
            assert confirm.await_count >= 1
            tasks.cancel_scope.cancel()
    assert renewed.is_set()
    assert control.current_context.expected_attempt_version > 1


async def _executor(admission, sessions, objects):
    run, _, _, execution, control, driver, _ = admission

    class Preparer:
        async def validate(self, context):
            await control.claim_state(run)

        def prepare(self, context):
            raise AssertionError("Harness reconstruction forbidden")

    committer = DatabaseRunTerminalCommitter(
        sessions,
        RunOutcomeService(sessions, RunPayloadStore(objects), lifecycle=test_lifecycle_writer()),
        execution,
    )
    capacity = Mock()
    executor = RunAttemptExecutor(
        context=control.current_context,
        control=control,
        driver=driver,
        preparer=Preparer(),
        wakeups=_Wakeups(),
        adapter=Mock(side_effect=AssertionError("Result adapter construction forbidden")),
        committer=committer,
        capacity_slot=capacity,
    )
    return executor, capacity


@pytest.mark.parametrize("failure", ["transient", "deadline", "permanent", "access_denied", "permission"])
async def test_preparation_failure_commits_fenced_retry_or_seal(
    admission, interaction_sessions, interaction_object_store, monkeypatch, failure
):
    run, _, states, _, control, driver, _ = admission
    if failure == "deadline":

        async def stalled(*args):
            await sleep_forever()

        read = AsyncMock(side_effect=stalled)
        monkeypatch.setattr(state_admission, "CLAIM_TOTAL_SECONDS", 0.05)
    else:
        error = (
            ObjectStoreUnavailable("temporary outage")
            if failure == "transient"
            else ObjectAccessDenied("access denied")
            if failure == "access_denied"
            else PermissionError("local access denied")
            if failure == "permission"
            else RunObjectIntegrityError("bad state")
        )
        read = AsyncMock(side_effect=error)
    monkeypatch.setattr(states, "read_run", read)
    executor, capacity = await _executor(admission, interaction_sessions, interaction_object_store)
    with fail_after(2):
        receipt = await executor.run()
    permanent = failure in {"permanent", "access_denied", "permission"}
    assert receipt.disposition.value == ("failed" if permanent else "retrying")
    assert read.await_count == (4 if failure == "transient" else 1)
    driver.run.assert_not_awaited()
    capacity.release.assert_called_once()
    async with short_session(interaction_sessions) as session:
        durable = await session.get(RunRecord, run.id)
        attempt = await session.get(RunAttemptRecord, control.current_context.run_attempt_id)
        attempts = (await session.scalars(select(RunAttemptRecord))).all()
        assert durable.current_run_attempt_id is None
        assert durable.status == ("failed" if permanent else "running")
        assert attempt.status == "failed"
        assert attempt.harness_run_id is None
        assert len(attempts) == 2


async def test_authority_loss_cancels_initial_read_without_stale_failure(
    admission, interaction_sessions, interaction_object_store, monkeypatch
):
    _, _, states, execution, _, driver, _ = admission
    read_started = Event()
    read_stopped = Event()

    async def stalled(*args):
        read_started.set()
        try:
            await sleep_forever()
        finally:
            read_stopped.set()

    async def lost(*args, **kwargs):
        await read_started.wait()
        raise AttemptAuthorityError("takeover won")

    monkeypatch.setattr(states, "read_run", stalled)
    monkeypatch.setattr(execution, "heartbeat", lost)
    failure = AsyncMock(wraps=execution.fail)
    monkeypatch.setattr(execution, "fail", failure)
    executor, capacity = await _executor(admission, interaction_sessions, interaction_object_store)
    with fail_after(2), pytest.raises(BaseExceptionGroup):
        await executor.run()
    assert read_stopped.is_set()
    failure.assert_not_awaited()
    driver.run.assert_not_awaited()
    capacity.release.assert_called_once()


async def test_repaired_outcome_is_adopted_under_current_fence_without_harness(
    admission, interaction_sessions, interaction_object_store, monkeypatch
):
    run, prior, states, _, control, driver, _ = admission
    entry_id = "inb_7777777777777777"
    payload = AcceptedAgentInput(schema_version="1", content=(TextContent(text="already incorporated"),))
    await ThreadInboxStore(interaction_sessions).append_steer(
        organization_id=run.organization_id, run_id=run.id, input=payload, entry_id=entry_id
    )
    entry = AdaptedThreadInboxEntry(
        1, ConsumedThreadInboxEntry(inbox_entry_id=entry_id, kind="steer"), "already incorporated"
    )
    monkeypatch.setattr(control._inbox, "_materialize", AsyncMock(return_value="already incorporated"))
    initial = await states.read_run(run)
    candidate = acceptance._completed_state(initial.envelope, prior.attempt.id, prior.attempt.fence)
    candidate = candidate.model_copy(
        update={
            "harness": HarnessState.new(
                thread_id=run.thread_id,
                message_history=[ModelRequest(parts=[UserPromptPart(entry.tagged_input(run.id))])],
            )
        }
    )
    await states.replace(initial, candidate, run_attempt_id=prior.attempt.id, fence=prior.attempt.fence)
    executor, capacity = await _executor(admission, interaction_sessions, interaction_object_store)
    monkeypatch.setattr(control, "fail_execution", AsyncMock(side_effect=AssertionError("unexpected recovery failure")))
    receipt = await executor.run()
    assert receipt.disposition.value == "completed"
    assert control.current_state.envelope.last_checkpoint_fence == control.current_context.fence
    assert control.current_state.envelope.outcome_candidate == candidate.outcome_candidate
    assert control.current_state.envelope.host.consumed_inbox_entries == (entry.receipt,)
    driver.run.assert_not_awaited()
    capacity.release.assert_called_once()
    async with short_session(interaction_sessions) as session:
        attempt = await session.get(RunAttemptRecord, control.current_context.run_attempt_id)
        assert attempt.status == "succeeded"
        assert attempt.harness_run_id is None
