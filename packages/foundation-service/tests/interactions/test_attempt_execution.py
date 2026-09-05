from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.hooks import InlineHookValidator
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.attempts import (
    AttemptAuthorityError,
    AttemptContext,
    AttemptExecutionService,
    AttemptPreparationAccepted,
    AttemptPreparationRejected,
)
from a13n_service.interactions.domain import (
    PendingCallKind,
    PendingCallSummary,
    RecoveryBudget,
    RecoveryUsage,
    Run,
    RunAttemptYieldReason,
    RunInputKind,
    RunLineageKind,
    RunPayloadObjectRef,
    RunPendingSummary,
    RunStatus,
    RunWaitReason,
    Session,
    Thread,
    ThreadOriginKind,
    ThreadRole,
)
from a13n_service.interactions.initialization import RunStateSeed, initialize_start_state
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunObjectIntegrityError, RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt, SealedClaim, WorkerClaim
from a13n_service.interactions.state import (
    CompletedOutcomeCandidate,
    DeferredContinuationState,
    HostContinuationState,
    RunPayloadEnvelope,
    RunStateEnvelope,
)
from a13n_service.lifecycle import LifecycleEventRecord
from a13n_service.storage import ObjectStore, short_session
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.lifecycle_support import test_lifecycle_writer

from .conftest import (
    AGENT_ID,
    AGENT_REVISION_ID,
    NOW,
    SESSION_ID,
    TENANT_ID,
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
    assert claim.attempt.fence == 1
    assert await scheduler.claim(run.id, worker) is None

    execution = AttemptExecutionService(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=2), lifecycle=test_lifecycle_writer()
    )
    authority = _authority(claim)
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    renewed = await execution.heartbeat(authority, lease_duration=timedelta(seconds=30))
    authority = _authority(
        claim,
        run_version=renewed.run_version,
        attempt_version=renewed.attempt_version,
    )
    entered = await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id="harness-run-1",
    )
    authority = _authority(claim, run_version=entered.run_version, attempt_version=entered.attempt_version)
    accounted = await execution.increment_model_request(authority)
    authority = _authority(claim, run_version=accounted.run_version, attempt_version=accounted.attempt_version)

    payloads = RunPayloadStore(interaction_object_store)
    output_reference: RunPayloadObjectRef | None = None
    if object_backed:
        output_reference = await payloads.create(
            TENANT_ID,
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
        claim.attempt.fence,
        outcome=(CompletedOutcomeCandidate(output_object=output_reference) if output_reference is not None else None),
    )
    stored = await execution.publish_checkpoint(authority, states, await states.read(TENANT_ID, run.id), candidate)
    outcome = await RunOutcomeService(
        interaction_sessions, payloads, clock=lambda: NOW + timedelta(seconds=3), lifecycle=test_lifecycle_writer()
    ).commit_state_outcome(authority, stored, expected_thread_version=1)

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
        assert await payloads.read(TENANT_ID, output_reference) == RunPayloadEnvelope(
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
    entered = await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id="harness-run-invalid-output",
    )
    authority = _authority(claim, run_version=entered.run_version, attempt_version=entered.attempt_version)
    candidate = _completed_state(
        state,
        claim.attempt.id,
        claim.attempt.fence,
        outcome=CompletedOutcomeCandidate(
            output_object=RunPayloadObjectRef(
                object_key=(f"tenants/{TENANT_ID}/runs/run_eeeeeeeeeeeeeeee/payloads/output/{'e' * 64}.json"),
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
        await states.read(TENANT_ID, run.id),
        candidate,
    )

    with pytest.raises(RunObjectIntegrityError, match="owned by the selected Run"):
        await RunOutcomeService(
            interaction_sessions,
            RunPayloadStore(interaction_object_store),
            clock=lambda: NOW + timedelta(seconds=3),
            lifecycle=test_lifecycle_writer(),
        ).commit_state_outcome(authority, stored, expected_thread_version=1)

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
    entered = await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id="harness-run-1",
    )
    authority = _authority(first, run_version=entered.run_version, attempt_version=entered.attempt_version)
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
    assert (second.attempt.attempt_number, second.attempt.fence) == (2, 2)
    assert second.attempt.recovery_reason == "lease_expired"
    async with short_session(interaction_sessions) as database:
        old = await database.get(RunAttemptRecord, first.attempt.id)
        current = await database.get(RunRecord, run.id)
        assert old is not None and current is not None
        assert old.status == "failed"
        assert old.failure_json["code"] == "run_attempt_lease_expired"
        assert current.usage_charged_json["model_requests"] == 1
        assert current.recovery_attempts_started == 2


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
        run_version=renewed.run_version,
        attempt_version=renewed.attempt_version,
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
    assert replacement.attempt.recovery_reason == "attempt_failed"
    assert replacement.attempt.replaces_run_attempt_id == claim.attempt.id
    async with short_session(interaction_sessions) as database:
        current = await database.get(RunRecord, run.id)
        assert current is not None
        assert (current.attempts_started, current.recovery_attempts_started) == (2, 2)


async def test_zero_recovery_budget_seals_without_creating_an_attempt(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(
        interaction_sessions,
        interaction_object_store,
        max_recovery_attempts=0,
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
        assert current.failure_json["code"] == "recovery_attempts_exhausted"
        assert thread.version == 2
        assert attempts == []


async def test_unknown_recovery_policy_fails_closed_before_attempt_creation(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(
        interaction_sessions,
        interaction_object_store,
        recovery_policy_version="future-policy",
    )

    result = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())

    assert isinstance(result, SealedClaim)
    assert result.failure.code == "recovery_policy_unsupported"
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
        recovery_deadline_at=NOW + timedelta(seconds=2),
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
    assert preparation.failure.code == "recovery_deadline_exhausted"
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
    before = await states.read(TENANT_ID, run.id)
    failure = SafeFailure(code="cancelled_by_user", message="The Run was cancelled.")

    receipt = await RunOutcomeService(
        interaction_sessions,
        RunPayloadStore(interaction_object_store),
        clock=lambda: NOW + timedelta(seconds=1),
        lifecycle=test_lifecycle_writer(),
    ).cancel(
        tenant_id=TENANT_ID,
        run_id=run.id,
        expected_run_version=1,
        expected_thread_version=1,
        failure=failure,
    )

    after = await states.read(TENANT_ID, run.id)
    assert receipt.run_status is RunStatus.cancelled
    assert after.info.version == before.info.version
    async with short_session(interaction_sessions) as database:
        current = await database.get(RunRecord, run.id)
        assert current is not None
        assert current.status == "cancelled"
        assert current.sealed_state_digest_sha256 is None


async def test_yield_prefers_a_different_build_without_consuming_recovery_budget(
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
    assert second.attempt.recovery_reason == "planned_handoff"
    assert second.attempt.replaces_run_attempt_id is None
    async with short_session(interaction_sessions) as database:
        current = await database.get(RunRecord, run.id)
        assert current is not None
        assert (current.attempts_started, current.recovery_attempts_started, current.handoffs_completed) == (2, 1, 1)
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
    entered = await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id="harness-run-waiting",
    )
    authority = _authority(claim, run_version=entered.run_version, attempt_version=entered.attempt_version)
    waiting = _waiting_state(state, claim.attempt.id, claim.attempt.fence)
    stored = await execution.publish_checkpoint(authority, states, await states.read(TENANT_ID, run.id), waiting)

    receipt = await RunOutcomeService(
        interaction_sessions,
        RunPayloadStore(interaction_object_store),
        clock=lambda: NOW + timedelta(seconds=3),
        lifecycle=test_lifecycle_writer(),
    ).commit_state_outcome(authority, stored, expected_thread_version=1)

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
    postgres_interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(postgres_interaction_sessions, interaction_object_store)
    first = AttemptScheduler(
        postgres_interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "token-1",
        attempt_id_factory=lambda: "rat_9999999999999999",
        lifecycle=test_lifecycle_writer(),
    )
    second = AttemptScheduler(
        postgres_interaction_sessions,
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
    async with short_session(postgres_interaction_sessions) as database:
        attempts = (await database.scalars(select(RunAttemptRecord))).all()
        current = await database.get(RunRecord, run.id)
        assert current is not None
        assert len(attempts) == 1
        assert current.current_run_attempt_id == attempts[0].id


async def _accept_root(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
    *,
    max_recovery_attempts: int = 3,
    recovery_policy_version: str = "1",
    recovery_deadline_at: datetime | None = None,
) -> tuple[RunStateStore, Run, RunStateEnvelope]:
    config = effective_agent_config()
    seed = RunStateSeed(
        run_id="run_5555555555555555",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=config,
    )
    state = initialize_start_state(seed, thread_id=THREAD_ID)
    run = Run(
        id=seed.run_id,
        version=1,
        tenant_id=TENANT_ID,
        authority_principal=PrincipalRef(principal_type=PrincipalType.user, principal_id=USER_ID),
        session_id=SESSION_ID,
        thread_id=state.thread_id,
        lineage_kind=RunLineageKind.root,
        trigger_type="user_input",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config_digest=config.content_digest,
        runtime_lock_digest=config.runtime_lock_digest,
        model_execution_observation=config.resolved_model.execution.observation(),
        priority=0,
        queue_name="default",
        available_at=NOW,
        next_attempt_fence=1,
        recovery_budget=RecoveryBudget(
            policy_version=recovery_policy_version,
            max_recovery_attempts=max_recovery_attempts,
            max_handoffs=2,
            recovery_deadline_at=recovery_deadline_at,
        ),
        attempts_started=0,
        recovery_attempts_started=0,
        handoffs_completed=0,
        usage_charged=RecoveryUsage(),
        request_fingerprint="1" * 64,
        status=RunStatus.accepted,
        input_kind=RunInputKind.agent_input,
        input={"schema_version": "1", "content": "hello"},
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
    ).accept_new_thread(
        session=Session(
            id=SESSION_ID,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            created_at=NOW,
            updated_at=NOW,
        ),
        thread=Thread(
            id=state.thread_id,
            version=1,
            queue_version=0,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            role=ThreadRole.root,
            origin_kind=ThreadOriginKind.new,
            current_run_id=run.id,
            created_at=NOW,
            updated_at=NOW,
        ),
        run=run,
        state=state,
    )
    return states, run, state


def _worker(
    *,
    worker_id: str = "worker-1",
    lease_seconds: int = 30,
    build_id: str = "build-1",
) -> WorkerClaim:
    return WorkerClaim(
        tenant_id=TENANT_ID,
        worker_id=worker_id,
        worker_generation=f"{worker_id}-generation",
        worker_build_id=build_id,
        runtime_lock_digest="a" * 64,
        lease_duration=timedelta(seconds=lease_seconds),
        handoff_preference_window=timedelta(seconds=30),
    )


def _authority(
    claim: ClaimedAttempt,
    *,
    run_version: int | None = None,
    attempt_version: int | None = None,
    lease_token: str | None = None,
) -> AttemptContext:
    lease_expires_at = claim.attempt.lease_expires_at
    lease_duration = lease_expires_at - claim.attempt.heartbeat_at
    return AttemptContext(
        tenant_id=TENANT_ID,
        thread_id=claim.thread_id,
        run_id=claim.attempt.run_id,
        run_attempt_id=claim.attempt.id,
        fence=claim.attempt.fence,
        lease_token=claim.lease_token if lease_token is None else lease_token,
        worker_id=claim.attempt.worker_id,
        worker_generation=claim.attempt.worker_generation,
        worker_build_id=claim.attempt.worker_build_id,
        runtime_lock_digest=claim.attempt.runtime_lock_digest,
        expected_run_version=claim.run_version if run_version is None else run_version,
        expected_attempt_version=claim.attempt.version if attempt_version is None else attempt_version,
        lease_expires_at=lease_expires_at,
        lease_duration=lease_duration,
        renewal_interval=lease_duration / 3,
        renewal_timeout=lease_duration / 6,
        reconciliation_timeout=timedelta(seconds=5),
        cleanup_timeout=timedelta(seconds=5),
    )


def _completed_state(
    previous: RunStateEnvelope,
    run_attempt_id: str,
    fence: int,
    *,
    outcome: CompletedOutcomeCandidate | None = None,
) -> RunStateEnvelope:
    payload = previous.model_dump(mode="python", by_alias=True)
    payload.update(
        checkpoint_seq=1,
        checkpoint_kind="completed",
        input_disposition="applied",
        last_checkpoint_run_attempt_id=run_attempt_id,
        last_checkpoint_fence=fence,
        outcome_candidate=outcome or CompletedOutcomeCandidate(output={"answer": 42}),
    )
    return type(previous).model_validate(payload)


def _waiting_state(previous: RunStateEnvelope, run_attempt_id: str, fence: int) -> RunStateEnvelope:
    payload = previous.model_dump(mode="python", by_alias=True)
    payload.update(
        checkpoint_seq=1,
        checkpoint_kind="waiting",
        input_disposition="applied",
        last_checkpoint_run_attempt_id=run_attempt_id,
        last_checkpoint_fence=fence,
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
    return RunStateEnvelope.model_validate(payload)


@pytest.mark.parametrize("revocation", ["lease_expired", "replaced", "cancelled", "iam_revoked"])
async def test_external_tool_scope_rechecks_durable_attempt_and_principal(
    interaction_sessions,
    interaction_object_store,
    monkeypatch,
    revocation,
):
    from unittest.mock import Mock

    import httpx2
    from a13n_service.connectivity import execution
    from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
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
        for kind, identifier, role in (("organization", TENANT_ID, "member"), ("workspace", WORKSPACE_ID, "admin")):
            database.add(
                RoleBindingRecord(
                    id=f"rb_tools_{kind}",
                    organization_id=TENANT_ID,
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
    context = _authority(claim)
    monkeypatch.setattr(execution, "utc_now", lambda: NOW)
    policy = EndpointPolicy()
    runtime = ExternalToolRuntime(
        interaction_sessions,
        SecretProtector(key=b"k" * 32, encryption_key_id="test"),
        ConnectorProviderRegistry(()),
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
            monkeypatch.setattr(execution, "utc_now", lambda: context.lease_expires_at)
        elif revocation == "replaced":
            attempt.fence += 1
        elif revocation == "iam_revoked":
            user = await database.get(UserRecord, USER_ID)
            user.status = "disabled"
    if revocation == "cancelled":
        async with short_session(interaction_sessions) as database:
            thread = await database.get(ThreadRecord, THREAD_ID)
        await RunOutcomeService(
            interaction_sessions, states, clock=lambda: NOW, lifecycle=test_lifecycle_writer()
        ).cancel(
            tenant_id=TENANT_ID,
            run_id=run.id,
            expected_run_version=context.expected_run_version,
            expected_thread_version=thread.version,
            failure=SafeFailure(code="cancelled", message="Cancelled by user."),
        )
    with pytest.raises((AttemptAuthorityError, AuthorizationError)):
        await runtime._scope(context)
