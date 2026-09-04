from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.hooks import InlineHookValidator
from a13n_service.interactions import (
    AttemptExecutionService,
    AttemptPreparationAccepted,
    HostContinuationState,
    RunInputKind,
    RunLineageKind,
    RunStateEnvelope,
)
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.control_domain import (
    ThreadInboxStatus,
    normalize_feedback,
)
from a13n_service.interactions.control_models import ThreadInboxCounterRecord, ThreadInboxRecord
from a13n_service.interactions.inbox import (
    DatabaseThreadInboxReconciler,
    RedisThreadControlSignals,
    ThreadInboxStore,
)
from a13n_service.interactions.inbox_persistence import ThreadInboxConflict
from a13n_service.interactions.initialization import RunStateSeed, initialize_waiting_continuation_state
from a13n_service.interactions.input import AcceptedAgentInput, TextContent
from a13n_service.interactions.models import ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.storage import ObjectStore, short_session
from fakeredis.aioredis import FakeRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import AGENT_ID, AGENT_REVISION_ID, NOW, TENANT_ID, effective_agent_config
from .test_acceptance import _accepted_run
from .test_attempt_execution import _accept_root, _authority, _waiting_state, _worker

pytestmark = pytest.mark.anyio


def _input(text: str) -> AcceptedAgentInput:
    return AcceptedAgentInput(schema_version="1", content=(TextContent(text=text),))


async def test_steer_accepts_before_or_after_claim_without_advancing_thread(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    store = ThreadInboxStore(interaction_sessions, clock=lambda: NOW + timedelta(seconds=2))

    first = await store.append_steer(
        tenant_id=TENANT_ID,
        run_id=run.id,
        input=_input("first"),
        entry_id="inb_1111111111111111",
    )

    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "lease-secret",
        attempt_id_factory=lambda: "rat_1111111111111111",
    )
    assert isinstance(await scheduler.claim(run.id, _worker()), ClaimedAttempt)
    second = await store.append_steer(
        tenant_id=TENANT_ID,
        run_id=run.id,
        input=_input("second"),
        entry_id="inb_2222222222222222",
    )

    assert (first.delivery_sequence, second.delivery_sequence) == (1, 2)
    status = await store.get_steer(tenant_id=TENANT_ID, run_id=run.id, steer_id=first.steer_id)
    assert status.status is ThreadInboxStatus.pending
    assert status.target_run_id == run.id
    async with short_session(interaction_sessions) as database:
        counter = await database.get(ThreadInboxCounterRecord, run.thread_id)
        thread = await database.get(ThreadRecord, run.thread_id)
        assert counter is not None and thread is not None
        assert (counter.next_delivery_sequence, counter.pending_count) == (3, 2)
        assert thread.version == 1
        assert thread.queue_version == 0


async def test_steer_capacity_rejection_is_atomic(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "lease-secret",
        attempt_id_factory=lambda: "rat_1212121212121212",
    )
    assert isinstance(await scheduler.claim(run.id, _worker()), ClaimedAttempt)
    store = ThreadInboxStore(
        interaction_sessions,
        max_pending_count=1,
        max_pending_bytes=1024,
        clock=lambda: NOW + timedelta(seconds=2),
    )
    await store.append_steer(
        tenant_id=TENANT_ID,
        run_id=run.id,
        input=_input("first"),
        entry_id="inb_1212121212121212",
    )

    with pytest.raises(ThreadInboxConflict, match="capacity"):
        await store.append_steer(
            tenant_id=TENANT_ID,
            run_id=run.id,
            input=_input("second"),
            entry_id="inb_1313131313131313",
        )

    async with short_session(interaction_sessions) as database:
        counter = await database.get(ThreadInboxCounterRecord, run.thread_id)
        rows = tuple((await database.scalars(select(ThreadInboxRecord))).all())
        assert counter is not None
        assert (counter.next_delivery_sequence, counter.pending_count) == (2, 1)
        assert [row.id for row in rows] == ["inb_1212121212121212"]


async def test_redis_control_stream_is_bounded_expiring_and_acknowledged_after_read() -> None:
    redis = FakeRedis()
    signals = RedisThreadControlSignals(redis, max_length=2, ttl_seconds=60)
    try:
        await signals.publish(tenant_id=TENANT_ID, thread_id="thread-11111111111111111111111111111111")
        await signals.publish(tenant_id=TENANT_ID, thread_id="thread-11111111111111111111111111111111")

        wakeups = await signals.read_new(
            tenant_id=TENANT_ID,
            thread_id="thread-11111111111111111111111111111111",
            consumer="worker-1-generation-1",
        )

        assert len(wakeups) == 2
        assert (
            await signals.acknowledge(
                tenant_id=TENANT_ID,
                thread_id="thread-11111111111111111111111111111111",
                stream_ids=tuple(item.stream_id for item in wakeups),
            )
            == 2
        )
        key = f"a13n:control:{TENANT_ID}:thread-11111111111111111111111111111111"
        assert 0 < await redis.ttl(key) <= 60
        assert await redis.xlen(key) <= 2
    finally:
        await redis.aclose()


async def test_redis_control_stream_reclaims_unacknowledged_wakeup() -> None:
    redis = FakeRedis()
    signals = RedisThreadControlSignals(redis, max_length=2, ttl_seconds=60)
    thread_id = "thread-12121212121212121212121212121212"
    try:
        await signals.publish(tenant_id=TENANT_ID, thread_id=thread_id)
        first = await signals.read_new(
            tenant_id=TENANT_ID,
            thread_id=thread_id,
            consumer="worker-1-generation-1",
        )

        reclaimed = await signals.claim_abandoned(
            tenant_id=TENANT_ID,
            thread_id=thread_id,
            consumer="worker-1-generation-2",
            min_idle_ms=0,
        )

        assert tuple(signal.stream_id for signal in reclaimed) == tuple(signal.stream_id for signal in first)
        assert (
            await signals.acknowledge(
                tenant_id=TENANT_ID,
                thread_id=thread_id,
                stream_ids=tuple(signal.stream_id for signal in reclaimed),
            )
            == 1
        )
    finally:
        await redis.aclose()


async def test_checkpoint_consumes_only_exact_fifo_prefix(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states, run, initial = await _accept_root(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "lease-secret",
        attempt_id_factory=lambda: "rat_2222222222222222",
    )
    claimed = await scheduler.claim(run.id, _worker())
    assert isinstance(claimed, ClaimedAttempt)
    authority = _authority(claimed)
    store = ThreadInboxStore(interaction_sessions, clock=lambda: NOW + timedelta(seconds=2))
    await store.append_steer(
        tenant_id=TENANT_ID,
        run_id=run.id,
        input=_input("first"),
        entry_id="inb_3333333333333333",
    )
    await store.append_steer(
        tenant_id=TENANT_ID,
        run_id=run.id,
        input=_input("second"),
        entry_id="inb_4444444444444444",
    )

    reconciler = DatabaseThreadInboxReconciler(
        interaction_sessions,
        lambda entry: _materialized(entry.payload),
        clock=lambda: NOW + timedelta(seconds=3),
    )
    entries = tuple(await reconciler.read_eligible(authority))
    assert [entry.input for entry in entries] == ["first", "second"]

    with pytest.raises(ThreadInboxConflict, match="FIFO prefix"):
        await reconciler.confirm_checkpoint(
            authority,
            await _publish_receipts(
                initial,
                states,
                authority,
                receipts=(entries[1].receipt,),
                sessions=interaction_sessions,
            ),
        )

    current = await states.read(TENANT_ID, run.id)
    successor = _state_with_receipts(current.envelope, authority, tuple(entry.receipt for entry in entries))
    stored = await AttemptExecutionService(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=4),
    ).publish_checkpoint(authority, states, current, successor)
    await reconciler.confirm_checkpoint(authority, stored)

    async with short_session(interaction_sessions) as database:
        rows = tuple(
            (await database.scalars(select(ThreadInboxRecord).order_by(ThreadInboxRecord.delivery_sequence))).all()
        )
        counter = await database.get(ThreadInboxCounterRecord, run.thread_id)
        assert counter is not None
        assert [row.status for row in rows] == ["consumed", "consumed"]
        assert all(row.consumed_state_digest_sha256 == stored.digest_sha256 for row in rows)
        assert counter.pending_count == 0
        assert counter.pending_bytes == 0


async def test_interrupt_supersedes_pending_steer_and_releases_budget(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "lease-secret",
        attempt_id_factory=lambda: "rat_3333333333333333",
    )
    claimed = await scheduler.claim(run.id, _worker())
    assert isinstance(claimed, ClaimedAttempt)
    store = ThreadInboxStore(interaction_sessions, clock=lambda: NOW + timedelta(seconds=2))
    steer = await store.append_steer(
        tenant_id=TENANT_ID,
        run_id=run.id,
        input=_input("stop before this"),
        entry_id="inb_5555555555555555",
    )

    receipt = await RunOutcomeService(
        interaction_sessions,
        RunPayloadStore(interaction_object_store),
        clock=lambda: NOW + timedelta(seconds=3),
    ).cancel(
        tenant_id=TENANT_ID,
        run_id=run.id,
        expected_run_version=claimed.run_version,
        expected_thread_version=1,
        failure=SafeFailure(code="run_interrupted", message="The Run was interrupted."),
    )

    assert receipt.run_status.value == "cancelled"
    status = await store.get_steer(tenant_id=TENANT_ID, run_id=run.id, steer_id=steer.steer_id)
    assert status.status is ThreadInboxStatus.superseded
    assert status.target_run_id is None
    async with short_session(interaction_sessions) as database:
        counter = await database.get(ThreadInboxCounterRecord, run.thread_id)
        assert counter is not None
        assert (counter.pending_count, counter.pending_bytes) == (0, 0)


async def test_waiting_outcome_rolls_delivery_and_feedback_binds_it_to_successor(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states, source, initial = await _accept_root(interaction_sessions, interaction_object_store)
    scheduler = AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "lease-secret",
        attempt_id_factory=lambda: "rat_4444444444444444",
    )
    claimed = await scheduler.claim(source.id, _worker())
    assert isinstance(claimed, ClaimedAttempt)
    execution = AttemptExecutionService(interaction_sessions, clock=lambda: NOW + timedelta(seconds=2))
    authority = _authority(claimed)
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    entered = await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id="harness-waiting",
    )
    authority = _authority(
        claimed,
        run_version=entered.run_version,
        attempt_version=entered.attempt_version,
    )
    store = ThreadInboxStore(interaction_sessions, clock=lambda: NOW + timedelta(seconds=3))
    steer = await store.append_steer(
        tenant_id=TENANT_ID,
        run_id=source.id,
        input=_input("after feedback"),
        entry_id="inb_6666666666666666",
    )
    waiting = _waiting_state(initial, claimed.attempt.id, claimed.attempt.fence)
    stored = await execution.publish_checkpoint(
        authority,
        states,
        await states.read(TENANT_ID, source.id),
        waiting,
    )
    outcome = await RunOutcomeService(
        interaction_sessions,
        RunPayloadStore(interaction_object_store),
        clock=lambda: NOW + timedelta(seconds=4),
    ).commit_state_outcome(authority, stored, expected_thread_version=1)
    assert outcome.run_status.value == "waiting"
    rolled = await store.get_steer(tenant_id=TENANT_ID, run_id=source.id, steer_id=steer.steer_id)
    assert rolled.target_run_id is None
    assert rolled.source_waiting_run_id == source.id

    candidate = waiting.outcome_candidate
    assert candidate is not None
    feedback = normalize_feedback(
        waiting_run_id=source.id,
        sealed_state_digest_sha256=stored.digest_sha256,
        pending=candidate.pending,
        submitted=(),
    )
    config = effective_agent_config()
    seed = RunStateSeed(
        run_id="run_7777777777777777",
        agent_id=AGENT_ID,
        agent_revision_id=AGENT_REVISION_ID,
        effective_agent_config=config,
    )
    successor_state = initialize_waiting_continuation_state(seed, waiting)
    successor = _accepted_run(
        run_id=seed.run_id,
        thread_id=source.thread_id,
        idempotency_key="feedback-successor",
        request_fingerprint="7" * 64,
    ).model_copy(
        update={
            "authority_principal": source.authority_principal,
            "connector_connection_selections": source.connector_connection_selections,
            "mcp_connection_selections": source.mcp_connection_selections,
            "ingress_context": source.ingress_context,
            "parent_run_id": source.id,
            "lineage_kind": RunLineageKind.continue_,
            "input_kind": RunInputKind.waiting_feedback,
            "input": feedback.model_dump(mode="json", by_alias=True, exclude_none=True),
        }
    )
    receipt = await RunAcceptanceService(
        interaction_sessions,
        RunStateStore(interaction_object_store),
        RunPayloadStore(interaction_object_store),
        InlineHookValidator(EndpointPolicy()),
        clock=lambda: NOW + timedelta(seconds=5),
    ).advance_thread(
        run=successor,
        state=successor_state,
        expected_thread_version=2,
        expected_current_run_id=source.id,
        expected_head_run_id=source.id,
        next_head_run_id=source.id,
    )

    assert receipt.thread_version == 3
    rebound = await store.get_steer(tenant_id=TENANT_ID, run_id=source.id, steer_id=steer.steer_id)
    assert rebound.status is ThreadInboxStatus.pending
    assert rebound.target_run_id == successor.id
    assert rebound.source_waiting_run_id == source.id


async def _materialized(payload) -> str:
    return payload["content"][0]["text"]


async def _publish_receipts(
    initial: RunStateEnvelope,
    states,
    authority,
    *,
    receipts,
    sessions: async_sessionmaker[AsyncSession],
):
    current = await states.read(TENANT_ID, initial.run_id)
    successor = _state_with_receipts(initial, authority, receipts)
    return await AttemptExecutionService(
        sessions,
        clock=lambda: NOW + timedelta(seconds=3),
    ).publish_checkpoint(authority, states, current, successor)


def _state_with_receipts(previous: RunStateEnvelope, authority, receipts) -> RunStateEnvelope:
    payload = previous.model_dump(mode="python", by_alias=True)
    payload.update(
        checkpoint_seq=previous.checkpoint_seq + 1,
        checkpoint_kind="progress",
        input_disposition="applied",
        last_checkpoint_run_attempt_id=authority.run_attempt_id,
        last_checkpoint_fence=authority.fence,
        host=HostContinuationState(consumed_inbox_entries=receipts),
        outcome_candidate=None,
    )
    return RunStateEnvelope.model_validate(payload)
