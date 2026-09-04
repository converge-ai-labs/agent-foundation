from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from a13n_harness import HarnessRunResult, HarnessRunResultEvent, HarnessState, SafeFailure
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions import (
    AttemptExecutionService,
    AttemptScheduler,
    CompletedOutcomeCandidate,
    RunPayloadEnvelope,
    RunPayloadObjectRef,
    RunPayloadStore,
    RunStateStore,
)
from a13n_service.interactions.control_domain import ThreadInboxEntry, ThreadInboxKind, ThreadInboxStatus
from a13n_service.interactions.control_models import ThreadInboxCounterRecord, ThreadInboxRecord
from a13n_service.interactions.inbox import DatabaseThreadInboxReconciler, ThreadInboxStore
from a13n_service.interactions.inbox_persistence import ThreadInboxCapacityExceeded
from a13n_service.interactions.input import AcceptedAgentInput, TextContent
from a13n_service.interactions.models import RunRecord
from a13n_service.run_stream import (
    LifecycleRunStreamProjector,
    RedisRunStream,
    RunReplayStore,
    RunStreamHarnessProjector,
)
from a13n_service.storage import ObjectStore, short_session, transaction
from a13n_service.subagents import (
    MAX_INLINE_ASYNC_RESULT_BYTES,
    AsyncSubagentResultError,
    AsyncSubagentResultInboxPayload,
    AsyncSubagentResultMaterializer,
    AsyncSubagentResultPublisher,
    ChildRunAcceptanceService,
)
from pydantic import TypeAdapter
from pydantic_ai.usage import RunUsage
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, TENANT_ID, effective_agent_config
from .test_attempt_execution import _authority, _worker
from .test_inbox import _state_with_receipts
from .test_subagent_acceptance import (
    _accept_parent,
    _complete_run,
    _grant_and_seed_child,
    _prepared_child,
)

pytestmark = pytest.mark.anyio

_RESULT_ADAPTER = TypeAdapter(AsyncSubagentResultInboxPayload)


@dataclass(slots=True)
class RecordingSignals:
    threads: list[tuple[str, str]] = field(default_factory=list)

    async def publish(self, *, tenant_id: str, thread_id: str) -> None:
        self.threads.append((tenant_id, thread_id))


async def test_sealed_child_result_reconciles_idempotently_into_active_fifo(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states, parent, authority, child_run_id = await _accept_child(
        interaction_sessions,
        interaction_object_store,
    )
    store = ThreadInboxStore(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=3),
    )
    await store.append_steer(
        tenant_id=TENANT_ID,
        run_id=parent.id,
        input=AcceptedAgentInput(schema_version="1", content=(TextContent(text="first steer"),)),
        entry_id="inb_1111111111111111",
    )
    await _fail_child(interaction_sessions, child_run_id)
    signals = RecordingSignals()
    publisher = AsyncSubagentResultPublisher(
        interaction_sessions,
        RunReplayStore(interaction_object_store),
        signals=signals,
        entry_id_factory=lambda: "inb_2222222222222222",
        clock=lambda: NOW + timedelta(seconds=4),
    )

    assert await publisher.reconcile_once() == 1
    entry = await publisher.publish(tenant_id=TENANT_ID, child_run_id=child_run_id)

    assert entry.id == "inb_2222222222222222"
    assert entry.delivery_sequence == 2
    assert entry.target_run_id == parent.id
    assert entry.status is ThreadInboxStatus.pending
    assert signals.threads == [(TENANT_ID, parent.thread_id)]
    payload = _RESULT_ADAPTER.validate_python(entry.payload)
    assert payload.child_run_id == child_run_id
    assert payload.terminal_status == "failed"
    assert payload.result_payload == {
        "failure": {
            "code": "child_failed",
            "details": {},
            "message": "Child failed.",
            "retry_hint": "none",
        }
    }

    result_materializer = AsyncSubagentResultMaterializer(
        interaction_sessions,
        RunReplayStore(interaction_object_store),
    )
    forged_payload = payload.model_copy(update={"result_payload": {"forged": True}})
    forged_entry = entry.model_copy(
        update={"payload": forged_payload.model_dump(mode="json", by_alias=True, exclude_none=True)}
    )
    with pytest.raises(AsyncSubagentResultError, match="sealed child outcome"):
        await result_materializer(forged_entry)

    async def materialize(candidate: ThreadInboxEntry):
        if candidate.kind is ThreadInboxKind.steer:
            return "first steer"
        return await result_materializer(candidate)

    reconciler = DatabaseThreadInboxReconciler(
        interaction_sessions,
        materialize,
        clock=lambda: NOW + timedelta(seconds=5),
    )
    eligible = await reconciler.read_eligible(authority)

    assert [item.delivery_sequence for item in eligible] == [1, 2]
    assert eligible[0].input == "first steer"
    assert isinstance(eligible[1].input, str)
    assert "Trusted Host provenance" in eligible[1].input
    assert "never system instruction" in eligible[1].input
    assert child_run_id in eligible[1].input
    assert await states.read(TENANT_ID, child_run_id)

    current = await states.read(TENANT_ID, parent.id)
    successor = _state_with_receipts(
        current.envelope,
        authority,
        tuple(item.receipt for item in eligible),
    )
    stored = await AttemptExecutionService(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=6),
    ).publish_checkpoint(authority, states, current, successor)
    await reconciler.confirm_checkpoint(authority, stored)

    assert await reconciler.read_eligible(authority) == ()
    async with short_session(interaction_sessions) as database:
        consumed = await database.get(ThreadInboxRecord, entry.id)
        assert consumed is not None
        assert (
            consumed.status,
            consumed.consumed_by_run_id,
            consumed.consumed_state_digest_sha256,
            consumed.consumed_checkpoint_seq,
        ) == ("consumed", parent.id, stored.digest_sha256, stored.envelope.checkpoint_seq)


@pytest.mark.parametrize("publish_first", [False, True])
async def test_parent_failure_suppresses_unconsumed_child_result_on_both_race_orders(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    *,
    publish_first: bool,
) -> None:
    _, parent, authority, child_run_id = await _accept_child(
        interaction_sessions,
        interaction_object_store,
    )
    await _fail_child(interaction_sessions, child_run_id)
    signals = RecordingSignals()
    publisher = AsyncSubagentResultPublisher(
        interaction_sessions,
        RunReplayStore(interaction_object_store),
        signals=signals,
        entry_id_factory=lambda: "inb_3333333333333333",
        clock=lambda: NOW + timedelta(seconds=4),
    )
    if publish_first:
        pending = await publisher.publish(tenant_id=TENANT_ID, child_run_id=child_run_id)
        assert pending.status is ThreadInboxStatus.pending
    await AttemptExecutionService(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=5),
    ).fail(
        authority,
        SafeFailure(code="parent_failed", message="Parent failed."),
        retryable=False,
    )

    entry = await publisher.publish(tenant_id=TENANT_ID, child_run_id=child_run_id)

    assert entry.status is ThreadInboxStatus.suppressed
    assert entry.target_run_id is None
    assert entry.finalized_at is not None
    async with short_session(interaction_sessions) as database:
        counter = await database.get(ThreadInboxCounterRecord, parent.thread_id)
        rows = tuple((await database.scalars(select(ThreadInboxRecord))).all())
        assert counter is not None
        assert (counter.next_delivery_sequence, counter.pending_count, counter.pending_bytes) == (2, 0, 0)
        assert len(rows) == 1
    assert signals.threads == ([(TENANT_ID, parent.thread_id)] if publish_first else [])


async def test_result_capacity_failure_leaves_no_partial_publication(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, parent, _, child_run_id = await _accept_child(
        interaction_sessions,
        interaction_object_store,
    )
    await ThreadInboxStore(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=3),
    ).append_steer(
        tenant_id=TENANT_ID,
        run_id=parent.id,
        input=AcceptedAgentInput(schema_version="1", content=(TextContent(text="fills inbox"),)),
        entry_id="inb_6666666666666666",
    )
    await _fail_child(interaction_sessions, child_run_id)
    publisher = AsyncSubagentResultPublisher(
        interaction_sessions,
        RunReplayStore(interaction_object_store),
        max_pending_count=1,
        entry_id_factory=lambda: "inb_7777777777777777",
        clock=lambda: NOW + timedelta(seconds=4),
    )

    with pytest.raises(ThreadInboxCapacityExceeded):
        await publisher.publish(tenant_id=TENANT_ID, child_run_id=child_run_id)
    assert await publisher.reconcile_once() == 0

    async with short_session(interaction_sessions) as database:
        counter = await database.get(ThreadInboxCounterRecord, parent.thread_id)
        rows = tuple((await database.scalars(select(ThreadInboxRecord))).all())
        assert counter is not None
        assert (counter.next_delivery_sequence, counter.pending_count) == (2, 1)
        assert [(row.id, row.kind) for row in rows] == [("inb_6666666666666666", "steer")]


async def test_inline_json_null_result_survives_inbox_and_materialization(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states, _, _, child_run_id = await _accept_child(
        interaction_sessions,
        interaction_object_store,
    )
    claim = await AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=3),
        token_factory=lambda: "null-child-lease",
        attempt_id_factory=lambda: "rat_cccccccccccccccc",
    ).claim(child_run_id, _worker())
    assert claim is not None
    async with short_session(interaction_sessions) as database:
        child = await database.get(RunRecord, child_run_id)
        assert child is not None
        child_resource = child.to_resource()
    await _complete_run(
        interaction_sessions,
        interaction_object_store,
        states,
        child_resource,
        _authority(claim),
        outcome=CompletedOutcomeCandidate(output=None),
        time_offset_seconds=4,
    )
    replays = RunReplayStore(interaction_object_store)

    entry = await AsyncSubagentResultPublisher(interaction_sessions, replays).publish(
        tenant_id=TENANT_ID,
        child_run_id=child_run_id,
    )
    payload = _RESULT_ADAPTER.validate_python(entry.payload)

    assert isinstance(entry.payload, dict) and "result_payload" in entry.payload
    assert "result_payload" in payload.model_fields_set and payload.result_payload is None
    projected = await AsyncSubagentResultMaterializer(interaction_sessions, replays)(entry)
    assert "<async-subagent-result-data>\nnull\n</async-subagent-result-data>" in projected


async def test_object_backed_result_requires_and_uses_authorized_terminal_item(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    redis_client: Redis,
) -> None:
    states, parent, _, child_run_id = await _accept_child(
        interaction_sessions,
        interaction_object_store,
    )
    replays, projector, output = await _complete_object_backed_child(
        interaction_sessions,
        interaction_object_store,
        redis_client,
        states,
        child_run_id,
    )
    publisher = AsyncSubagentResultPublisher(interaction_sessions, replays)

    with pytest.raises(AsyncSubagentResultError, match="authorized terminal result Item"):
        await publisher.publish(
            tenant_id=TENANT_ID,
            child_run_id=child_run_id,
        )
    assert await publisher.reconcile_once() == 0

    async with short_session(interaction_sessions) as database:
        counter = await database.get(ThreadInboxCounterRecord, parent.thread_id)
        rows = tuple((await database.scalars(select(ThreadInboxRecord))).all())
        assert counter is not None
        assert (counter.next_delivery_sequence, counter.pending_count, counter.pending_bytes) == (1, 0, 0)
        assert rows == ()

    await _project_all_lifecycle(projector)
    snapshot = await replays.read(TENANT_ID, child_run_id)
    entry = await publisher.publish(tenant_id=TENANT_ID, child_run_id=child_run_id)
    result = _RESULT_ADAPTER.validate_python(entry.payload)
    output_item = snapshot.items[-1]
    output_reference = RunPayloadObjectRef.model_validate(output_item.content)

    assert result.terminal_result_item_id == output_item.id
    assert result.result_digest == output_reference.digest_sha256
    assert "result_payload" not in result.model_fields_set
    materializer = AsyncSubagentResultMaterializer(interaction_sessions, replays)
    projected = await materializer(entry)
    assert output_item.id in projected
    assert output not in projected

    forged_entry = entry.model_copy(
        update={
            "payload": {
                **result.as_json(),
                "terminal_result_item_id": "item_ffffffffffffffff",
            }
        }
    )
    with pytest.raises(AsyncSubagentResultError, match="retained terminal event"):
        await materializer(forged_entry)


async def test_result_publication_reauthorizes_the_spawning_principal(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, parent, _, child_run_id = await _accept_child(
        interaction_sessions,
        interaction_object_store,
    )
    await _fail_child(interaction_sessions, child_run_id)
    async with transaction(interaction_sessions) as database:
        binding = await database.scalar(
            select(RoleBindingRecord).where(RoleBindingRecord.principal_id == parent.authority_principal.principal_id)
        )
        assert binding is not None
        await database.delete(binding)

    with pytest.raises(AsyncSubagentResultError, match="publication is no longer authorized"):
        await AsyncSubagentResultPublisher(
            interaction_sessions,
            RunReplayStore(interaction_object_store),
        ).publish(
            tenant_id=TENANT_ID,
            child_run_id=child_run_id,
        )

    async with short_session(interaction_sessions) as database:
        counter = await database.get(ThreadInboxCounterRecord, parent.thread_id)
        rows = tuple((await database.scalars(select(ThreadInboxRecord))).all())
        assert counter is not None
        assert (counter.next_delivery_sequence, counter.pending_count, counter.pending_bytes) == (1, 0, 0)
        assert rows == ()


async def test_active_delivery_never_bypasses_an_earlier_unbound_result(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    _, parent, authority, child_run_id = await _accept_child(
        interaction_sessions,
        interaction_object_store,
    )
    store = ThreadInboxStore(interaction_sessions, clock=lambda: NOW + timedelta(seconds=3))
    await store.append_steer(
        tenant_id=TENANT_ID,
        run_id=parent.id,
        input=AcceptedAgentInput(schema_version="1", content=(TextContent(text="first"),)),
        entry_id="inb_8888888888888888",
    )
    await _fail_child(interaction_sessions, child_run_id)
    result = await AsyncSubagentResultPublisher(
        interaction_sessions,
        RunReplayStore(interaction_object_store),
        entry_id_factory=lambda: "inb_9999999999999999",
        clock=lambda: NOW + timedelta(seconds=4),
    ).publish(tenant_id=TENANT_ID, child_run_id=child_run_id)
    await store.append_steer(
        tenant_id=TENANT_ID,
        run_id=parent.id,
        input=AcceptedAgentInput(schema_version="1", content=(TextContent(text="third"),)),
        entry_id="inb_aaaaaaaaaaaaaaaa",
    )
    async with transaction(interaction_sessions) as database:
        row = await database.get(ThreadInboxRecord, result.id)
        assert row is not None
        row.target_run_id = None

    async def materialize(entry: ThreadInboxEntry) -> str:
        return f"entry-{entry.delivery_sequence}"

    reconciler = DatabaseThreadInboxReconciler(
        interaction_sessions,
        materialize,
        clock=lambda: NOW + timedelta(seconds=5),
    )

    eligible = await reconciler.read_eligible(authority)

    assert [entry.delivery_sequence for entry in eligible] == [1]


async def test_concurrent_result_publication_converges_on_postgresql(
    postgres_interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    sessions = postgres_interaction_sessions
    _, parent, _, child_run_id = await _accept_child(sessions, interaction_object_store)
    await _fail_child(sessions, child_run_id)
    ids = iter(("inb_4444444444444444", "inb_5555555555555555"))
    signals = RecordingSignals()
    publisher = AsyncSubagentResultPublisher(
        sessions,
        RunReplayStore(interaction_object_store),
        signals=signals,
        entry_id_factory=lambda: next(ids),
        clock=lambda: NOW + timedelta(seconds=4),
    )

    entries = await asyncio.gather(
        publisher.publish(tenant_id=TENANT_ID, child_run_id=child_run_id),
        publisher.publish(tenant_id=TENANT_ID, child_run_id=child_run_id),
    )

    assert entries[0] == entries[1]
    async with short_session(sessions) as database:
        rows = tuple(
            (
                await database.scalars(
                    select(ThreadInboxRecord).where(
                        ThreadInboxRecord.kind == ThreadInboxKind.async_subagent_result.value
                    )
                )
            ).all()
        )
        assert len(rows) == 1
    assert signals.threads == [(TENANT_ID, parent.thread_id)]


async def _accept_child(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
):
    await _grant_and_seed_child(sessions)
    states, parent, parent_state = await _accept_parent(sessions, objects)
    scheduler = AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "parent-lease",
        attempt_id_factory=lambda: "rat_aaaaaaaaaaaaaaaa",
    )
    claim = await scheduler.claim(parent.id, _worker())
    assert claim is not None
    authority = _authority(claim)
    async with short_session(sessions) as database:
        parent_record = await database.get(RunRecord, parent.id)
        assert parent_record is not None
        running_parent = parent_record.to_resource()
    child_config = effective_agent_config()
    prepared = _prepared_child(
        running_parent,
        parent_state,
        authority.run_attempt_id,
        authority.fence,
        child_config,
        suffix="c",
    )
    accepted = await ChildRunAcceptanceService(
        sessions,
        RunStateStore(objects),
        RunPayloadStore(objects),
        clock=lambda: NOW + timedelta(seconds=2),
    ).accept(prepared, authority)
    return states, running_parent, authority, accepted.child_run_id


async def _fail_child(sessions: async_sessionmaker[AsyncSession], child_run_id: str) -> None:
    failure = SafeFailure(code="child_failed", message="Child failed.")
    async with transaction(sessions) as database:
        child = await database.get(RunRecord, child_run_id)
        assert child is not None
        child.status = "failed"
        child.failure_json = failure.model_dump(mode="json", by_alias=True, exclude_none=True)
        child.sealed_at = NOW + timedelta(seconds=3)
        child.updated_at = NOW + timedelta(seconds=3)
        child.version += 1


async def _complete_object_backed_child(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
    redis: Redis,
    states: RunStateStore,
    child_run_id: str,
) -> tuple[RunReplayStore, LifecycleRunStreamProjector, str]:
    claim = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=3),
        token_factory=lambda: "child-lease",
        attempt_id_factory=lambda: "rat_bbbbbbbbbbbbbbbb",
    ).claim(child_run_id, _worker())
    assert claim is not None
    async with short_session(sessions) as database:
        child = await database.get(RunRecord, child_run_id)
        assert child is not None
        child_resource = child.to_resource()
    output = "x" * (MAX_INLINE_ASYNC_RESULT_BYTES + 1)
    payloads = RunPayloadStore(objects)
    output_object = await payloads.create(
        TENANT_ID,
        RunPayloadEnvelope(
            run_id=child_run_id,
            payload_kind="output",
            payload_schema_version="1",
            payload=output,
        ),
    )
    stream = RedisRunStream(redis)
    live_projector = RunStreamHarnessProjector(
        stream,
        tenant_id=TENANT_ID,
        run_id=child_run_id,
        thread_id=child_resource.thread_id,
        run_attempt_id=claim.attempt.id,
        harness_run_id="completed-child",
    )
    live_projector.project(
        HarnessRunResultEvent(
            thread_id=child_resource.thread_id,
            run_id="completed-child",
            sequence=0,
            occurred_at=NOW + timedelta(seconds=4),
            result=HarnessRunResult(
                thread_id=child_resource.thread_id,
                run_id="completed-child",
                status="completed",
                output=output,
                state=HarnessState.new(thread_id=child_resource.thread_id),
                usage=RunUsage(),
            ),
        )
    )
    await live_projector.close()
    await _complete_run(
        sessions,
        objects,
        states,
        child_resource,
        _authority(claim),
        outcome=CompletedOutcomeCandidate(output_object=output_object),
        time_offset_seconds=4,
    )
    replays = RunReplayStore(objects)
    projector = LifecycleRunStreamProjector(
        sessions,
        stream,
        replays,
        worker_id="subagent-result-test-projector",
        clock=lambda: NOW + timedelta(seconds=5),
    )
    return replays, projector, output


async def _project_all_lifecycle(projector: LifecycleRunStreamProjector) -> None:
    for _ in range(100):
        if await projector.project_once(limit=200) == 0:
            return
    raise AssertionError("lifecycle projection did not drain")
