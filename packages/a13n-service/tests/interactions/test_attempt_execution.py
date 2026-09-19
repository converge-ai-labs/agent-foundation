from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.environments.domain import ExistingEnvironmentSelection
from a13n_service.environments.websocket.coordination import ConnectionCoordination
from a13n_service.hooks import InlineHookValidator
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.attempts import (
    AttemptAuthorityError,
    AttemptContext,
    AttemptExecutionService,
    AttemptLease,
    AttemptPreparationAccepted,
    AttemptPreparationRejected,
)
from a13n_service.interactions.domain import (
    ExecutionBudget,
    PendingCallKind,
    PendingCallSummary,
    Run,
    RunAttemptYieldReason,
    RunInputKind,
    RunLineageKind,
    RunPayloadObjectRef,
    RunPendingSummary,
    RunStatus,
    RunUsage,
    RunWaitReason,
    Session,
    Thread,
    ThreadOriginKind,
    ThreadRole,
)
from a13n_service.interactions.environment_selection import EnvironmentDefault, ExplicitEnvironment
from a13n_service.interactions.harness_results import AttemptDisposition
from a13n_service.interactions.initialization import RunStateSeed, initialize_start_state
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunObjectIntegrityError, RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeError, RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt, SealedClaim, WorkerClaim
from a13n_service.interactions.state import (
    CompletedOutcomeCandidate,
    DeferredContinuationState,
    HostContinuationState,
    RunCheckpoint,
    RunPayloadEnvelope,
)
from a13n_service.interactions.terminal_committer import DatabaseAttemptCommitter
from a13n_service.lifecycle import LifecycleEventRecord
from a13n_service.storage import ObjectStore, short_session
from anyio import Event, create_task_group, fail_after
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.lifecycle_support import test_lifecycle_writer
from tests.memory.selection_support import ordinary_memory

from .conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    NOW,
    ORGANIZATION_ID,
    SESSION_ID,
    THREAD_ID,
    USER_ID,
    WORKSPACE_ID,
    effective_agent_config,
)

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("object_backed", [False, True])
async def test_claim_execute_checkpoint_and_complete_atomically(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    *,
    object_backed: bool,
) -> None:
    states, run, state = await _accept_root(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "lease-secret",
        attempt_id_factory=lambda: "rat_1111111111111111",
        lifecycle=test_lifecycle_writer(),
    )
    worker = _worker()

    assert await scheduler.scan(worker, queue_name="default") == (run.id,)
    result = await scheduler.claim(run.id, worker)
    assert isinstance(result, ClaimedAttempt)
    claim = result
    assert claim.attempt.attempt_number == 1
    assert await scheduler.claim(run.id, worker) is None

    execution = AttemptExecutionService(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=2), lifecycle=test_lifecycle_writer()
    )
    authority = _authority(claim)
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    await execution.heartbeat(authority, lease_duration=timedelta(seconds=30))
    await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id="harness-run-1",
    )
    await execution.increment_model_request(authority)

    payloads = RunPayloadStore(interaction_object_store)
    output_reference: RunPayloadObjectRef | None = None
    if object_backed:
        output_reference = await payloads.create(
            ORGANIZATION_ID,
            RunPayloadEnvelope(
                run_id=run.id,
                payload_kind="output",
                payload_schema_version="1",
                payload={"answer": 42},
            ),
        )
    candidate = _completed_state(
        state,
        claim.attempt.id,
        claim.attempt.attempt_number,
        outcome=(CompletedOutcomeCandidate(output_object=output_reference) if output_reference is not None else None),
    )
    stored = await execution.publish_checkpoint(
        authority, states, await states.read(ORGANIZATION_ID, run.id), candidate
    )
    outcome = await RunOutcomeService(
        interaction_sessions, payloads, clock=lambda: NOW + timedelta(seconds=3), lifecycle=test_lifecycle_writer()
    ).commit_state_outcome(authority, stored)

    assert outcome.run_status is RunStatus.completed
    assert outcome.thread_version == 2
    async with short_session(interaction_sessions) as database:
        run_record = await database.get(RunRecord, run.id)
        attempt = await database.get(RunAttemptRecord, claim.attempt.id)
        thread = await database.get(ThreadRecord, run.thread_id)
        assert run_record is not None and attempt is not None and thread is not None
        completed = run_record.to_resource()
        assert completed.status is RunStatus.completed
        if output_reference is None:
            assert completed.output == {"answer": 42}
        else:
            assert completed.output_object == output_reference
        assert run_record.usage_charged_json["model_requests"] == 1
        assert attempt.status == "succeeded"
        assert (thread.head_run_id, thread.current_run_id, thread.version) == (run.id, run.id, 2)
        events = (
            await database.scalars(
                select(LifecycleEventRecord)
                .where(LifecycleEventRecord.run_id == run.id)
                .order_by(LifecycleEventRecord.seq)
            )
        ).all()
        assert tuple(event.event_type for event in events) == (
            "run.accepted",
            "run_attempt.leased",
            "run.running",
            "run_attempt.running",
            "run_attempt.succeeded",
            "run.completed",
        )
        assert tuple(event.resource_seq for event in events if event.entity_type == "run") == (1, 2, 3)
        assert tuple(event.resource_seq for event in events if event.entity_type == "run_attempt") == (1, 2, 3)
        completed = next(event for event in events if event.event_type == "run.completed")
        succeeded = next(event for event in events if event.event_type == "run_attempt.succeeded")
        assert completed.payload["final_run_attempt_id"] == claim.attempt.id
        assert succeeded.payload["resulting_run_lifecycle_event_id"] == completed.id
    if output_reference is not None:
        assert await payloads.read(ORGANIZATION_ID, output_reference) == RunPayloadEnvelope(
            run_id=run.id,
            payload_kind="output",
            payload_schema_version="1",
            payload={"answer": 42},
        )


async def test_completed_outcome_rejects_output_payload_owned_by_another_run(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states, run, state = await _accept_root(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "lease-secret",
        attempt_id_factory=lambda: "rat_dddddddddddddddd",
        lifecycle=test_lifecycle_writer(),
    )
    claim = await scheduler.claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    execution = AttemptExecutionService(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=2), lifecycle=test_lifecycle_writer()
    )
    authority = _authority(claim)
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id="harness-run-invalid-output",
    )
    candidate = _completed_state(
        state,
        claim.attempt.id,
        claim.attempt.attempt_number,
        outcome=CompletedOutcomeCandidate(
            output_object=RunPayloadObjectRef(
                object_key=(
                    f"organizations/{ORGANIZATION_ID}/runs/run_eeeeeeeeeeeeeeee/payloads/output/{'e' * 64}.json"
                ),
                digest_sha256="e" * 64,
                size_bytes=123,
                content_type="application/vnd.converge.run-payload+json",
                schema_version="1",
            )
        ),
    )
    stored = await execution.publish_checkpoint(
        authority,
        states,
        await states.read(ORGANIZATION_ID, run.id),
        candidate,
    )

    with pytest.raises(RunObjectIntegrityError, match="owned by the selected Run"):
        await RunOutcomeService(
            interaction_sessions,
            RunPayloadStore(interaction_object_store),
            clock=lambda: NOW + timedelta(seconds=3),
            lifecycle=test_lifecycle_writer(),
        ).commit_state_outcome(authority, stored)

    async with short_session(interaction_sessions) as database:
        current = await database.get(RunRecord, run.id)
        attempt = await database.get(RunAttemptRecord, claim.attempt.id)
        assert current is not None and attempt is not None
        assert (current.status, attempt.status) == ("running", "running")


async def test_expired_attempt_is_failed_charged_and_replaced_with_a_higher_fence(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    ids = iter(("rat_2222222222222222", "rat_3333333333333333"))
    first_scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "first-token",
        attempt_id_factory=lambda: next(ids),
        lifecycle=test_lifecycle_writer(),
    )
    first = await first_scheduler.claim(run.id, _worker(lease_seconds=1))
    assert isinstance(first, ClaimedAttempt)
    execution = AttemptExecutionService(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1, milliseconds=100),
        lifecycle=test_lifecycle_writer(),
    )
    authority = _authority(first)
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id="harness-run-1",
    )
    await execution.increment_model_request(authority)

    takeover = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=3),
        token_factory=lambda: "second-token",
        attempt_id_factory=lambda: next(ids),
        lifecycle=test_lifecycle_writer(),
    )
    second = await takeover.claim(run.id, _worker(worker_id="worker-2"))

    assert isinstance(second, ClaimedAttempt)
    assert (second.attempt.attempt_number, second.attempt.attempt_number) == (2, 2)
    assert second.attempt.start_reason == "lease_expired"
    async with short_session(interaction_sessions) as database:
        old = await database.get(RunAttemptRecord, first.attempt.id)
        current = await database.get(RunRecord, run.id)
        assert old is not None and current is not None
        assert old.status == "failed"
        assert old.failure_json["code"] == "run_attempt_lease_expired"
        assert current.usage_charged_json["model_requests"] == 1
        assert current.attempts_charged == 2


async def test_retryable_failure_backoff_and_stale_authority_are_enforced(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "lease-secret",
        attempt_id_factory=lambda: "rat_4444444444444444",
        lifecycle=test_lifecycle_writer(),
    )
    claim = await scheduler.claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    execution = AttemptExecutionService(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=2), lifecycle=test_lifecycle_writer()
    )

    validated = await execution.validate(_authority(claim))
    assert (validated.run_version, validated.attempt_version) == (
        claim.run_version,
        claim.attempt.version,
    )
    with pytest.raises(AttemptAuthorityError):
        await execution.validate(_authority(claim, lease_token="wrong-token"))

    with pytest.raises(AttemptAuthorityError):
        await execution.heartbeat(
            _authority(claim, lease_token="wrong-token"),
            lease_duration=timedelta(seconds=30),
        )

    renewed = await execution.heartbeat(
        _authority(claim),
        lease_duration=timedelta(seconds=30),
    )
    assert renewed.run_version == claim.run_version
    assert renewed.lease_expires_at == NOW + timedelta(seconds=32)
    authority = _authority(
        claim,
    )

    failure = SafeFailure(code="dependency_unavailable", message="A dependency is temporarily unavailable.")
    receipt = await execution.fail(
        authority,
        failure,
        retryable=True,
        retry_after=timedelta(seconds=5),
    )
    assert receipt.run_version == claim.run_version + 1
    async with short_session(interaction_sessions) as database:
        current = await database.get(RunRecord, run.id)
        attempt = await database.get(RunAttemptRecord, claim.attempt.id)
        thread = await database.get(ThreadRecord, run.thread_id)
        assert current is not None and attempt is not None and thread is not None
        assert (current.status, current.current_run_attempt_id) == ("running", None)
        assert current.to_resource().available_at == NOW + timedelta(seconds=7)
        assert attempt.status == "failed"
        assert thread.version == 1
        events = (
            await database.scalars(
                select(LifecycleEventRecord)
                .where(LifecycleEventRecord.run_id == run.id)
                .order_by(LifecycleEventRecord.seq)
            )
        ).all()
        assert tuple(event.event_type for event in events) == (
            "run.accepted",
            "run_attempt.leased",
            "run.running",
            "run_attempt.failed",
        )

    recovery = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=8),
        token_factory=lambda: "recovery-token",
        attempt_id_factory=lambda: "rat_cccccccccccccccc",
        lifecycle=test_lifecycle_writer(),
    )
    replacement = await recovery.claim(run.id, _worker(worker_id="worker-2"))
    assert isinstance(replacement, ClaimedAttempt)
    assert replacement.attempt.start_reason == "attempt_failed"
    assert replacement.attempt.replaces_run_attempt_id == claim.attempt.id
    async with short_session(interaction_sessions) as database:
        current = await database.get(RunRecord, run.id)
        assert current is not None
        assert (current.attempts_started, current.attempts_charged) == (2, 2)


@pytest.mark.parametrize(
    ("failure_code", "deadline_seconds", "retries"),
    [
        ("skill_materialization_stale", None, True),
        ("skill_materialization_unavailable", None, True),
        ("skill_materialization_invalid", None, False),
        ("skill_materialization_stale", 3, False),
    ],
)
async def test_skill_failure_recovery_respects_budget_and_fencing(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    failure_code: str,
    deadline_seconds: int | None,
    retries: bool,
) -> None:
    _, run, _ = await _accept_root(
        interaction_sessions,
        interaction_object_store,
        max_attempts=2,
        execution_deadline_at=NOW + timedelta(seconds=deadline_seconds) if deadline_seconds else None,
    )
    now = NOW + timedelta(seconds=1)
    scheduler = AttemptScheduler(interaction_sessions, clock=lambda: now, lifecycle=test_lifecycle_writer())
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: now, lifecycle=test_lifecycle_writer())
    outcomes = RunOutcomeService(
        interaction_sessions, RunPayloadStore(interaction_object_store), lifecycle=test_lifecycle_writer()
    )
    committer = DatabaseAttemptCommitter(interaction_sessions, outcomes, execution)
    claim = await scheduler.claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    now += timedelta(seconds=1)
    failure = SafeFailure(code=failure_code, message="Skill preparation failed.")
    result = await committer.commit_failure(_authority(claim), failure)
    assert result.disposition is (AttemptDisposition.retrying if retries else AttemptDisposition.failed)
    if retries:
        now += timedelta(seconds=2)
        replacement = await scheduler.claim(run.id, _worker(worker_id="worker-2"))
        assert isinstance(replacement, ClaimedAttempt)
        assert replacement.attempt.replaces_run_attempt_id == claim.attempt.id
        with pytest.raises(AttemptAuthorityError):
            await committer.commit_failure(_authority(claim), failure)
        await execution.validate(_authority(replacement))
        result = await committer.commit_failure(_authority(replacement), failure)
        assert result.disposition is AttemptDisposition.failed
    async with short_session(interaction_sessions) as database:
        current = await database.get(RunRecord, run.id)
        assert current is not None
        assert current.status == "failed"
        assert current.current_run_attempt_id is None
        assert current.attempts_started == current.attempts_charged == (2 if retries else 1)
        assert current.failure_json["code"] == failure_code


async def test_zero_execution_budget_seals_without_creating_an_attempt(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(
        interaction_sessions,
        interaction_object_store,
        max_attempts=0,
    )
    scheduler = AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    )

    result = await scheduler.claim(run.id, _worker())

    assert isinstance(result, SealedClaim)
    async with short_session(interaction_sessions) as database:
        current = await database.get(RunRecord, run.id)
        thread = await database.get(ThreadRecord, run.thread_id)
        attempts = (await database.scalars(select(RunAttemptRecord))).all()
        assert current is not None and thread is not None
        assert current.status == "failed"
        assert current.failure_json["code"] == "execution_attempts_exhausted"
        assert thread.version == 2
        assert attempts == []


async def test_unknown_recovery_policy_fails_closed_before_attempt_creation(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(
        interaction_sessions,
        interaction_object_store,
        execution_policy_version="future-policy",
    )

    result = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())

    assert isinstance(result, SealedClaim)
    assert result.failure.code == "execution_policy_unsupported"
    async with short_session(interaction_sessions) as database:
        current = await database.get(RunRecord, run.id)
        attempts = (await database.scalars(select(RunAttemptRecord))).all()
        assert current is not None
        assert current.status == "failed"
        assert attempts == []


async def test_preparation_rechecks_fixed_deadline_and_fails_closed(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(
        interaction_sessions,
        interaction_object_store,
        execution_deadline_at=NOW + timedelta(seconds=2),
    )
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "lease-secret",
        attempt_id_factory=lambda: "rat_bbbbbbbbbbbbbbbb",
        lifecycle=test_lifecycle_writer(),
    )
    claim = await scheduler.claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)

    preparation = await AttemptExecutionService(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=3), lifecycle=test_lifecycle_writer()
    ).commit_preparation_success(_authority(claim))

    assert isinstance(preparation, AttemptPreparationRejected)
    assert preparation.failure.code == "execution_deadline_exhausted"
    async with short_session(interaction_sessions) as database:
        current = await database.get(RunRecord, run.id)
        attempt = await database.get(RunAttemptRecord, claim.attempt.id)
        thread = await database.get(ThreadRecord, run.thread_id)
        assert current is not None and attempt is not None and thread is not None
        assert current.status == "failed"
        assert attempt.status == "failed"
        assert thread.version == 2


async def test_cancel_seals_without_state_replacement(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    before = await states.read(ORGANIZATION_ID, run.id)
    failure = SafeFailure(code="cancelled_by_user", message="The Run was cancelled.")

    receipt = await RunOutcomeService(
        interaction_sessions,
        RunPayloadStore(interaction_object_store),
        clock=lambda: NOW + timedelta(seconds=1),
        lifecycle=test_lifecycle_writer(),
    ).cancel(
        organization_id=ORGANIZATION_ID,
        run_id=run.id,
        expected_run_version=1,
        expected_thread_version=1,
        failure=failure,
    )

    after = await states.read(ORGANIZATION_ID, run.id)
    assert receipt.run_status is RunStatus.cancelled
    assert after.info.version == before.info.version
    async with short_session(interaction_sessions) as database:
        current = await database.get(RunRecord, run.id)
        assert current is not None
        assert current.status == "cancelled"
        assert current.sealed_state_digest_sha256 is None


async def test_yield_prefers_a_different_build_without_consuming_execution_budget(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    ids = iter(("rat_6666666666666666", "rat_7777777777777777"))
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "first-token",
        attempt_id_factory=lambda: next(ids),
        lifecycle=test_lifecycle_writer(),
    )
    first = await scheduler.claim(run.id, _worker())
    assert isinstance(first, ClaimedAttempt)
    await AttemptExecutionService(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=2), lifecycle=test_lifecycle_writer()
    ).yield_attempt(_authority(first), reason=RunAttemptYieldReason.service_drain)

    same_build = AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=3), lifecycle=test_lifecycle_writer()
    )
    assert await same_build.scan(_worker(), queue_name="default") == ()
    assert await same_build.claim(run.id, _worker()) is None

    new_build = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=3),
        token_factory=lambda: "second-token",
        attempt_id_factory=lambda: next(ids),
        lifecycle=test_lifecycle_writer(),
    )
    second = await new_build.claim(run.id, _worker(build_id="build-2"))
    assert isinstance(second, ClaimedAttempt)
    assert second.attempt.start_reason == "planned_handoff"
    assert second.attempt.replaces_run_attempt_id is None
    async with short_session(interaction_sessions) as database:
        current = await database.get(RunRecord, run.id)
        assert current is not None
        assert (current.attempts_started, current.attempts_charged, current.handoffs_completed) == (2, 1, 1)
        run_events = (
            await database.scalars(
                select(LifecycleEventRecord)
                .where(LifecycleEventRecord.run_id == run.id, LifecycleEventRecord.entity_type == "run")
                .order_by(LifecycleEventRecord.resource_seq)
            )
        ).all()
        assert tuple(event.event_type for event in run_events) == ("run.accepted", "run.running")


async def _wait_for_approval(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
):
    states, run, state = await _accept_root(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "lease-secret",
        attempt_id_factory=lambda: "rat_8888888888888888",
        lifecycle=test_lifecycle_writer(),
    )
    claim = await scheduler.claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    execution = AttemptExecutionService(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=2), lifecycle=test_lifecycle_writer()
    )
    authority = _authority(claim)
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id="harness-run-waiting",
    )
    waiting = _waiting_state(state, claim.attempt.id, claim.attempt.attempt_number)
    stored = await execution.publish_checkpoint(authority, states, await states.read(ORGANIZATION_ID, run.id), waiting)

    receipt = await RunOutcomeService(
        interaction_sessions,
        RunPayloadStore(interaction_object_store),
        clock=lambda: NOW + timedelta(seconds=3),
        lifecycle=test_lifecycle_writer(),
    ).commit_state_outcome(authority, stored)

    return run, receipt


async def test_waiting_outcome_selects_the_frozen_continuation_head(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    run, receipt = await _wait_for_approval(interaction_sessions, interaction_object_store)
    assert receipt.run_status is RunStatus.waiting
    async with short_session(interaction_sessions) as database:
        current = await database.get(RunRecord, run.id)
        thread = await database.get(ThreadRecord, run.thread_id)
        assert current is not None and thread is not None
        assert (current.status, current.wait_reason) == ("waiting", "approval")
        assert current.pending_json["calls"][0]["call_id"] == "approval-1"
        assert (thread.head_run_id, thread.current_run_id) == (run.id, run.id)


async def test_postgresql_concurrent_claim_has_exactly_one_winner(
    interaction_sessions: async_sessionmaker[AsyncSession], interaction_object_store: ObjectStore
) -> None:
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    first = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "token-1",
        attempt_id_factory=lambda: "rat_9999999999999999",
        lifecycle=test_lifecycle_writer(),
    )
    second = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "token-2",
        attempt_id_factory=lambda: "rat_aaaaaaaaaaaaaaaa",
        lifecycle=test_lifecycle_writer(),
    )

    results = await asyncio.gather(
        first.claim(run.id, _worker(worker_id="worker-1")),
        second.claim(run.id, _worker(worker_id="worker-2")),
    )

    winners = [result for result in results if isinstance(result, ClaimedAttempt)]
    assert len(winners) == 1
    async with short_session(interaction_sessions) as database:
        attempts = (await database.scalars(select(RunAttemptRecord))).all()
        current = await database.get(RunRecord, run.id)
        assert current is not None
        assert len(attempts) == 1
        assert current.current_run_attempt_id == attempts[0].id


async def _accept_root(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
    *,
    config: EffectiveAgentConfig | None = None,
    session_id: str = SESSION_ID,
    thread_id: str = THREAD_ID,
    run_id: str = "run_5555555555555555",
    max_attempts: int = 3,
    execution_policy_version: str = "1",
    execution_deadline_at: datetime | None = None,
    environment_id: str | None = None,
    coordination: ConnectionCoordination | None = None,
    devices=None,
    environment_working_directory: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[RunStateStore, Run, RunCheckpoint]:
    config = config or effective_agent_config()
    seed = RunStateSeed(
        run_id=run_id,
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=config,
    )
    state = initialize_start_state(seed, thread_id=thread_id)
    run = Run(
        id=seed.run_id,
        version=1,
        organization_id=ORGANIZATION_ID,
        authority_principal=PrincipalRef(principal_type=PrincipalType.user, principal_id=USER_ID),
        session_id=session_id,
        thread_id=state.thread_id,
        lineage_kind=RunLineageKind.root,
        trigger_type="user_input",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config_digest=config.content_digest,
        model_execution_observation=config.resolved_model.execution.observation(),
        environment_id=environment_id,
        priority=0,
        queue_name="default",
        available_at=NOW,
        execution_budget=ExecutionBudget(
            policy_version=execution_policy_version,
            max_attempts=max_attempts,
            max_handoffs=2,
            execution_deadline_at=execution_deadline_at,
        ),
        attempts_started=0,
        attempts_charged=0,
        handoffs_completed=0,
        usage_charged=RunUsage(),
        request_fingerprint="1" * 64,
        idempotency_key=idempotency_key,
        status=RunStatus.accepted,
        input_kind=RunInputKind.agent_input,
        input={"schema_version": "1", "content": [{"type": "text", "text": "hello"}]},
        created_at=NOW,
        updated_at=NOW,
    )
    states = RunStateStore(objects)
    await RunAcceptanceService(
        sessions,
        states,
        RunPayloadStore(objects),
        InlineHookValidator(EndpointPolicy()),
        clock=lambda: NOW,
        lifecycle=test_lifecycle_writer(),
        bindings=ordinary_memory(sessions),
        coordination=coordination,
        devices=devices,
    ).accept_new_thread(
        session=Session(
            id=session_id,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            created_at=NOW,
            updated_at=NOW,
        ),
        thread=Thread(
            id=state.thread_id,
            version=1,
            queue_version=0,
            organization_id=ORGANIZATION_ID,
            session_id=session_id,
            role=ThreadRole.root,
            origin_kind=ThreadOriginKind.new,
            current_run_id=run.id,
            created_at=NOW,
            updated_at=NOW,
        ),
        run=run,
        state=state,
        environment=ExplicitEnvironment(
            ExistingEnvironmentSelection(
                environment_id=environment_id,
                working_directory=environment_working_directory,
            )
        )
        if environment_id is not None
        else EnvironmentDefault.agent,
    )
    return states, run, state


def _worker(
    *,
    worker_id: str = "worker-1",
    lease_seconds: int = 30,
    build_id: str = "build-1",
) -> WorkerClaim:
    return WorkerClaim(
        organization_id=ORGANIZATION_ID,
        worker_id=worker_id,
        worker_build_id=build_id,
        lease_duration=timedelta(seconds=lease_seconds),
        handoff_preference_window=timedelta(seconds=30),
    )


def _authority(
    claim: ClaimedAttempt,
    *,
    lease_token: str | None = None,
) -> AttemptContext:
    lease_expires_at = claim.attempt.lease_expires_at
    lease_duration = lease_expires_at - claim.attempt.heartbeat_at
    return AttemptContext(
        organization_id=ORGANIZATION_ID,
        thread_id=claim.thread_id,
        run_id=claim.attempt.run_id,
        run_attempt_id=claim.attempt.id,
        attempt_number=claim.attempt.attempt_number,
        lease_token=claim.lease_token if lease_token is None else lease_token,
        worker_id=claim.attempt.worker_id,
        worker_build_id=claim.attempt.worker_build_id,
        lease_duration=lease_duration,
        lease=AttemptLease(lease_expires_at),
        renewal_interval=lease_duration / 3,
        renewal_timeout=lease_duration / 6,
        reconciliation_timeout=timedelta(seconds=5),
        cleanup_timeout=timedelta(seconds=5),
    )


def _completed_state(
    previous: RunCheckpoint,
    run_attempt_id: str,
    attempt_number: int,
    *,
    outcome: CompletedOutcomeCandidate | None = None,
) -> RunCheckpoint:
    payload = previous.model_dump(mode="python", by_alias=True)
    payload.update(
        checkpoint_seq=1,
        checkpoint_kind="completed",
        last_checkpoint_run_attempt_id=run_attempt_id,
        last_checkpoint_fence=attempt_number,
        outcome_candidate=outcome or CompletedOutcomeCandidate(output={"answer": 42}),
    )
    return type(previous).model_validate(payload)


def _waiting_state(previous: RunCheckpoint, run_attempt_id: str, attempt_number: int) -> RunCheckpoint:
    payload = previous.model_dump(mode="python", by_alias=True)
    payload.update(
        checkpoint_seq=1,
        checkpoint_kind="waiting",
        last_checkpoint_run_attempt_id=run_attempt_id,
        last_checkpoint_fence=attempt_number,
        host=HostContinuationState(
            deferred=DeferredContinuationState(
                requests={
                    "calls": [],
                    "approvals": [
                        {
                            "tool_name": "dangerous_tool",
                            "args": {},
                            "tool_call_id": "approval-1",
                        }
                    ],
                    "metadata": {},
                }
            )
        ),
        outcome_candidate={
            "outcome": "waiting",
            "wait_reason": RunWaitReason.approval,
            "pending": RunPendingSummary(
                calls=(
                    PendingCallSummary(
                        call_id="approval-1",
                        kind=PendingCallKind.approval,
                        tool_name="dangerous_tool",
                    ),
                )
            ),
        },
    )
    return RunCheckpoint.model_validate(payload)


@pytest.mark.parametrize("revocation", ["lease_expired", "replaced", "cancelled", "iam_revoked", "heartbeat"])
async def test_external_tool_scope_rechecks_durable_attempt_and_principal(
    interaction_sessions,
    interaction_object_store,
    monkeypatch,
    revocation,
):
    from unittest.mock import Mock

    import httpx2
    from a13n_harness.providers.catalog import ProviderCatalog
    from a13n_service.connectivity import execution
    from a13n_service.connectivity.execution import ExternalToolRuntime
    from a13n_service.connectivity.mcp.refresh import OAuthCredentialRefresh
    from a13n_service.connectivity.mcp.transport import RemoteTransport
    from a13n_service.iam import AuthorizationError
    from a13n_service.iam.models import RoleBindingRecord, UserRecord
    from a13n_service.secrets import SecretProtector
    from a13n_service.storage import transaction

    states, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    async with transaction(interaction_sessions) as database:
        database.add(
            UserRecord(
                id=USER_ID,
                email="tool@example.com",
                normalized_email="tool@example.com",
                name="Tool User",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await database.flush()
        for kind, identifier, role in (
            ("organization", ORGANIZATION_ID, "member"),
            ("workspace", WORKSPACE_ID, "admin"),
        ):
            database.add(
                RoleBindingRecord(
                    id=f"rb_tools_{kind}",
                    organization_id=ORGANIZATION_ID,
                    workspace_id=WORKSPACE_ID if kind == "workspace" else None,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type=kind,
                    resource_id=identifier,
                    role_key=role,
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
    claim = await AttemptScheduler(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    from a13n_service.iam.attempts import AttemptAuthorizationError

    from .worker_helpers import prepare_permissions

    context = await prepare_permissions(interaction_sessions, run, _authority(claim))
    monkeypatch.setattr(execution, "utc_now", lambda: NOW)
    policy = EndpointPolicy()
    runtime = ExternalToolRuntime(
        interaction_sessions,
        SecretProtector(key=b"k" * 32, encryption_key_id="test"),
        ProviderCatalog(),
        None,
        RemoteTransport(policy),
        policy,
        Mock(spec=httpx2.AsyncClient),
        Mock(spec=OAuthCredentialRefresh),
    )
    scope = await runtime._scope(context)
    assert scope.actor.principal.principal_id == USER_ID
    async with transaction(interaction_sessions) as database:
        attempt = await database.get(RunAttemptRecord, claim.attempt.id)
        if revocation == "lease_expired":
            monkeypatch.setattr(execution, "utc_now", lambda: claim.attempt.lease_expires_at)
        elif revocation == "replaced":
            attempt.attempt_number += 1
        elif revocation == "iam_revoked":
            user = await database.get(UserRecord, USER_ID)
            user.status = "disabled"
    if revocation == "cancelled":
        async with short_session(interaction_sessions) as database:
            thread = await database.get(ThreadRecord, THREAD_ID)
            current = await database.get(RunRecord, run.id)
        await RunOutcomeService(
            interaction_sessions, states, clock=lambda: NOW, lifecycle=test_lifecycle_writer()
        ).cancel(
            organization_id=ORGANIZATION_ID,
            run_id=run.id,
            expected_run_version=current.version,
            expected_thread_version=thread.version,
            failure=SafeFailure(code="cancelled", message="Cancelled by user."),
        )
    if revocation == "iam_revoked":
        assert await runtime._scope(context) == scope
        for _ in range(10):
            await context.authorization.admit_model_request()
        with pytest.raises(AttemptAuthorizationError, match="attempt_authorization_denied"):
            await context.authorization.admit_model_request()
    if revocation == "heartbeat":
        renewed = await AttemptExecutionService(
            interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()
        ).heartbeat(context, lease_duration=timedelta(seconds=30))
        assert renewed.attempt_version == claim.attempt.version + 1
        assert await runtime._scope(context) == scope
    else:
        with pytest.raises((AttemptAuthorityError, AuthorizationError, AttemptAuthorizationError)):
            await runtime._scope(context)


@pytest.mark.parametrize("lease_expires", [False, True])
async def test_output_verification_uses_fresh_lease_and_versions_before_sealing(
    interaction_sessions, interaction_object_store: ObjectStore, monkeypatch, lease_expires: bool
) -> None:
    states, run, envelope = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(interaction_sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    now = NOW + timedelta(seconds=1)
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: now, lifecycle=test_lifecycle_writer())
    authority = _authority(claim)
    prepared = await execution.commit_preparation_success(authority)
    assert isinstance(prepared, AttemptPreparationAccepted)
    await execution.enter_harness(authority, preparation=prepared, harness_run_id="harness-output-check")

    payloads = RunPayloadStore(interaction_object_store)
    reference = await payloads.create(
        ORGANIZATION_ID,
        RunPayloadEnvelope(run_id=run.id, payload_kind="output", payload_schema_version="1", payload={"answer": 42}),
    )
    candidate = _completed_state(
        envelope,
        claim.attempt.id,
        claim.attempt.attempt_number,
        outcome=CompletedOutcomeCandidate(output_object=reference),
    )
    stored = await execution.publish_checkpoint(
        authority, states, await states.read(ORGANIZATION_ID, run.id), candidate
    )
    outcomes = RunOutcomeService(interaction_sessions, payloads, clock=lambda: now, lifecycle=test_lifecycle_writer())
    reading, release = Event(), Event()
    verify_reference = payloads.verify_reference
    reads = 0

    async def slow_verify(*args, **kwargs):
        nonlocal reads
        reads += 1
        reading.set()
        await release.wait()
        return await verify_reference(*args, **kwargs)

    monkeypatch.setattr(payloads, "verify_reference", slow_verify)
    verified = []

    async def verify():
        verified.append(await outcomes.verify_state_outcome(authority, stored))

    with fail_after(5):
        async with create_task_group() as tasks:
            tasks.start_soon(verify)
            await reading.wait()
            receipt = await execution.heartbeat(authority, lease_duration=timedelta(seconds=30))
            if lease_expires:
                now = receipt.lease_expires_at
            release.set()
    another_service = RunOutcomeService(
        interaction_sessions, payloads, clock=lambda: now, lifecycle=test_lifecycle_writer()
    )
    with pytest.raises(RunOutcomeError, match="Output verification"):
        await another_service.commit_verified_state_outcome(authority, verified[0])
    if lease_expires:
        with pytest.raises(AttemptAuthorityError):
            await outcomes.commit_verified_state_outcome(authority, verified[0])
        async with short_session(interaction_sessions) as database:
            record = await database.get(RunRecord, run.id)
            assert record is not None and record.status == "running" and record.to_resource().sealed_state is None
    else:
        result = await outcomes.commit_verified_state_outcome(authority, verified[0])
        assert result.run_status is RunStatus.completed and result.thread_version == 2
    assert reads == 1


async def test_postgresql_heartbeat_does_not_revoke_tool_authority(
    interaction_sessions, interaction_object_store, monkeypatch
):
    await test_external_tool_scope_rechecks_durable_attempt_and_principal(
        interaction_sessions, interaction_object_store, monkeypatch, "heartbeat"
    )
