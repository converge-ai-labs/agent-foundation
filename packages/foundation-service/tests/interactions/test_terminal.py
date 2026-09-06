from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest
from a13n_harness import SafeFailure
from a13n_service.interactions._outcome_transitions import RunOutcomePreconditionChanged
from a13n_service.interactions.attempts import (
    AttemptAuthorityError,
    AttemptExecutionService,
    AttemptPreparationAccepted,
)
from a13n_service.interactions.harness_results import RunTerminalDisposition
from a13n_service.interactions.inbox import ThreadInboxStore
from a13n_service.interactions.input import AcceptedAgentInput, TextContent
from a13n_service.interactions.models import RunAttemptRecord, RunRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.interactions.state import CompletedOutcomeCandidate, RunPayloadEnvelope
from a13n_service.interactions.terminal import DatabaseRunTerminalCommitter
from a13n_service.storage import short_session
from anyio import Event, create_task_group, fail_after

from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, ORGANIZATION_ID
from .test_attempt_execution import _accept_root, _authority, _completed_state, _waiting_state, _worker

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("candidate_kind", ["waiting", "completed"])
@pytest.mark.parametrize("pending_delivery", [False, True])
async def test_terminal_adapter_adopts_predecessor_and_reconciles_exact_committed_state(
    interaction_sessions, interaction_object_store, candidate_kind, pending_delivery
):
    states, run, initial = await _accept_root(interaction_sessions, interaction_object_store)
    first = await AttemptScheduler(interaction_sessions, lifecycle=test_lifecycle_writer(), clock=lambda: NOW).claim(
        run.id, _worker(lease_seconds=1)
    )
    assert isinstance(first, ClaimedAttempt)
    first_execution = AttemptExecutionService(
        interaction_sessions, lifecycle=test_lifecycle_writer(), clock=lambda: NOW
    )
    authority = _authority(first)
    preparation = await first_execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    entered = await first_execution.enter_harness(authority, preparation=preparation, harness_run_id="harness-first")
    authority = _authority(first, run_version=entered.run_version, attempt_version=entered.attempt_version)
    candidate = (_completed_state if candidate_kind == "completed" else _waiting_state)(initial, first.attempt.id, 1)
    published = await first_execution.publish_checkpoint(
        authority, states, await states.read(ORGANIZATION_ID, run.id), candidate
    )

    def clock():
        return NOW + timedelta(seconds=2)

    second = await AttemptScheduler(interaction_sessions, lifecycle=test_lifecycle_writer(), clock=clock).claim(
        run.id, _worker(worker_id="worker-2")
    )
    assert isinstance(second, ClaimedAttempt)
    authority = _authority(second)
    execution = AttemptExecutionService(interaction_sessions, lifecycle=test_lifecycle_writer(), clock=clock)
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    state = await execution.claim_state_writer(authority, states, published)
    outcomes = RunOutcomeService(
        interaction_sessions, RunPayloadStore(interaction_object_store), lifecycle=test_lifecycle_writer(), clock=clock
    )
    committer = DatabaseRunTerminalCommitter(interaction_sessions, outcomes, execution, clock=clock)
    if pending_delivery:
        await ThreadInboxStore(interaction_sessions, clock=clock).append_steer(
            organization_id=ORGANIZATION_ID,
            run_id=run.id,
            input=AcceptedAgentInput(schema_version="1", content=(TextContent(text="One more request."),)),
            entry_id="inb_1234567890abcdef",
        )
        if candidate_kind == "completed":
            receipt = await committer.commit_state_outcome(authority, state, preparation=preparation)
            assert receipt.disposition is RunTerminalDisposition.retrying
            async with short_session(interaction_sessions) as database:
                stored_run = await database.get(RunRecord, run.id)
                assert stored_run.status == "running"
                assert stored_run.current_run_attempt_id is None
                assert stored_run.sealed_state_digest_sha256 is None
            assert await states.read(ORGANIZATION_ID, run.id) == state
            return
    receipt = await committer.commit_state_outcome(authority, state, preparation=preparation)
    assert receipt.disposition.value == candidate_kind
    assert await committer.commit_state_outcome(authority, state, preparation=preparation) == receipt
    with pytest.raises(AttemptAuthorityError):
        await committer.commit_state_outcome(replace(authority, lease_token="wrong"), state, preparation=preparation)
    with pytest.raises(AttemptAuthorityError):
        await committer.commit_state_outcome(authority, published, preparation=preparation)
    async with short_session(interaction_sessions) as database:
        attempt = await database.get(RunAttemptRecord, second.attempt.id)
        assert attempt.status == "succeeded"
        assert attempt.harness_run_id is None
        stored_run = await database.get(RunRecord, run.id)
        assert stored_run.sealed_state_digest_sha256 == state.digest_sha256


async def test_terminal_adapter_never_invents_durable_cancellation(interaction_sessions, interaction_object_store):
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(interaction_sessions, lifecycle=test_lifecycle_writer(), clock=lambda: NOW).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    authority = _authority(claim)
    outcomes = RunOutcomeService(
        interaction_sessions,
        RunPayloadStore(interaction_object_store),
        lifecycle=test_lifecycle_writer(),
        clock=lambda: NOW,
    )
    execution = AttemptExecutionService(interaction_sessions, lifecycle=test_lifecycle_writer(), clock=lambda: NOW)
    committer = DatabaseRunTerminalCommitter(interaction_sessions, outcomes, execution, clock=lambda: NOW)
    with pytest.raises(AttemptAuthorityError):
        await committer.reconcile_cancelled(authority)
    await outcomes.cancel(
        organization_id=ORGANIZATION_ID,
        run_id=run.id,
        expected_run_version=claim.run_version,
        expected_thread_version=1,
        failure=SafeFailure(code="interrupted", message="Interrupted."),
    )
    receipt = await committer.reconcile_cancelled(authority)
    assert receipt.disposition is RunTerminalDisposition.cancelled
    assert receipt.thread_version == 2


async def test_failure_receipt_captures_atomic_thread_transition(interaction_sessions, interaction_object_store):
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(interaction_sessions, lifecycle=test_lifecycle_writer(), clock=lambda: NOW).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    execution = AttemptExecutionService(interaction_sessions, lifecycle=test_lifecycle_writer(), clock=lambda: NOW)
    committer = DatabaseRunTerminalCommitter(
        interaction_sessions,
        RunOutcomeService(
            interaction_sessions,
            RunPayloadStore(interaction_object_store),
            lifecycle=test_lifecycle_writer(),
            clock=lambda: NOW,
        ),
        execution,
        clock=lambda: NOW,
    )
    receipt = await committer.commit_failure(
        _authority(claim), SafeFailure(code="invalid_state", message="Invalid state.")
    )
    assert receipt.disposition is RunTerminalDisposition.failed
    assert receipt.thread_version == 2


async def test_thread_precondition_reconciliation_is_bounded(interaction_sessions, interaction_object_store):
    states, run, initial = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(interaction_sessions, lifecycle=test_lifecycle_writer(), clock=lambda: NOW).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)

    class ChangingThread(RunOutcomeService):
        calls = 0

        async def commit_verified_state_outcome(self, *args, **kwargs):
            self.calls += 1
            raise RunOutcomePreconditionChanged("Thread changed")

    outcomes = ChangingThread(
        interaction_sessions,
        RunPayloadStore(interaction_object_store),
        lifecycle=test_lifecycle_writer(),
        clock=lambda: NOW,
    )
    committer = DatabaseRunTerminalCommitter(
        interaction_sessions,
        outcomes,
        AttemptExecutionService(interaction_sessions, lifecycle=test_lifecycle_writer(), clock=lambda: NOW),
        clock=lambda: NOW,
    )
    state = await AttemptExecutionService(
        interaction_sessions, lifecycle=test_lifecycle_writer(), clock=lambda: NOW
    ).publish_checkpoint(
        _authority(claim),
        states,
        await states.read(ORGANIZATION_ID, run.id),
        _completed_state(initial, claim.attempt.id, 1),
    )
    with pytest.raises(RunOutcomePreconditionChanged):
        await committer.commit_state_outcome(_authority(claim), state)
    assert outcomes.calls == 3


async def test_output_verification_keeps_renewing_and_commits_with_fresh_authority(
    interaction_sessions, interaction_object_store, monkeypatch
):
    states, run, initial = await _accept_root(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(interaction_sessions, lifecycle=test_lifecycle_writer(), clock=lambda: NOW)
    first = await scheduler.claim(run.id, _worker(lease_seconds=1))
    assert isinstance(first, ClaimedAttempt)
    payloads = RunPayloadStore(interaction_object_store)
    output = await payloads.create(
        ORGANIZATION_ID,
        RunPayloadEnvelope(run_id=run.id, payload_kind="output", payload_schema_version="1", payload="output"),
    )
    candidate = _completed_state(initial, first.attempt.id, 1).model_copy(
        update={"outcome_candidate": CompletedOutcomeCandidate(output_object=output)}
    )
    await AttemptExecutionService(
        interaction_sessions, lifecycle=test_lifecycle_writer(), clock=lambda: NOW
    ).publish_checkpoint(_authority(first), states, await states.read(ORGANIZATION_ID, run.id), candidate)

    def clock():
        return NOW + timedelta(seconds=2)

    claim = await AttemptScheduler(interaction_sessions, lifecycle=test_lifecycle_writer(), clock=clock).claim(
        run.id, _worker(worker_id="worker-2")
    )
    assert isinstance(claim, ClaimedAttempt)
    execution = AttemptExecutionService(interaction_sessions, lifecycle=test_lifecycle_writer(), clock=clock)
    control = RunAttemptControl(context=_authority(claim), execution=execution, states=states, inbox=Mock())
    await control.load_state()
    preparation = await control.commit_preparation()
    assert isinstance(preparation, AttemptPreparationAccepted)
    committer = DatabaseRunTerminalCommitter(
        interaction_sessions,
        RunOutcomeService(interaction_sessions, payloads, lifecycle=test_lifecycle_writer(), clock=clock),
        execution,
        clock=clock,
    )
    verifying, renewed = Event(), Event()
    verify = payloads.verify_reference

    async def slow_verification(*args, **kwargs):
        verifying.set()
        await renewed.wait()
        return await verify(*args, **kwargs)

    monkeypatch.setattr(payloads, "verify_reference", slow_verification)
    receipts = []

    async def adopt():
        receipts.append(await control.adopt_prepared_outcome(preparation, committer=committer))

    with fail_after(2):
        async with create_task_group() as tasks:
            tasks.start_soon(adopt)
            await verifying.wait()
            await control.renew_lease()
            renewed.set()
    assert len(receipts) == 1
    assert receipts[0].disposition is RunTerminalDisposition.completed
    assert control.current_context.expected_attempt_version > claim.attempt.version
