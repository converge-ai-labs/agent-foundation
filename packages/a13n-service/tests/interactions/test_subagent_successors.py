from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from a13n_service.interactions.attempts import AttemptExecutionService, AttemptPreparationAccepted
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.domain import Run, RunInputKind
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.run_stream import RunReplayStore
from a13n_service.storage import ObjectStore, short_session, transaction
from a13n_service.subagents import (
    AsyncSubagentResultError,
    AsyncSubagentResultInboxPayload,
    AsyncSubagentResultPublisher,
    AsyncSubagentSuccessorReconciler,
    ChildRunAcceptanceService,
    prepare_child_run,
    project_accepted_async_subagent_result,
)
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, ORGANIZATION_ID, effective_agent_config
from .test_attempt_execution import _completed_state, _waiting_state
from .test_subagent_acceptance import CHILD_AGENT_ID, CHILD_DEFINITION_ID, CHILD_REVISION_ID
from .test_subagent_results import (
    _accept_child,
    _complete_object_backed_child,
    _fail_child,
    _project_all_lifecycle,
)

pytestmark = pytest.mark.anyio


async def test_completed_parent_result_accepts_exact_checkpoint_zero_successor(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states, parent, authority, child_run_id = await _accept_child(
        interaction_sessions,
        interaction_object_store,
    )
    completed_parent = await _seal_parent(
        interaction_sessions,
        interaction_object_store,
        states,
        parent,
        authority,
        outcome="completed",
    )
    await _fail_child(interaction_sessions, child_run_id)
    result = await AsyncSubagentResultPublisher(
        interaction_sessions,
        RunReplayStore(interaction_object_store),
        entry_id_factory=lambda: "inb_bbbbbbbbbbbbbbbb",
        clock=lambda: NOW + timedelta(seconds=5),
    ).publish(organization_id=ORGANIZATION_ID, child_run_id=child_run_id)
    assert result.target_run_id is None and result.source_waiting_run_id is None

    async with transaction(interaction_sessions) as database:
        current_thread = await database.get(ThreadRecord, parent.thread_id)
        current_thread.labels = {"team": "latest"}
    receipt = await AsyncSubagentSuccessorReconciler(
        interaction_sessions,
        states,
        RunReplayStore(interaction_object_store),
        run_id_factory=lambda _organization, _entry, _parent: "run_bbbbbbbbbbbbbbbb",
        clock=lambda: NOW + timedelta(seconds=6),
        lifecycle=test_lifecycle_writer(),
    ).reconcile_thread(organization_id=ORGANIZATION_ID, thread_id=parent.thread_id)

    assert receipt.outcome == "run_accepted"
    assert receipt.successor is not None
    successor_id = receipt.successor.run_id
    successor_state = await states.read(ORGANIZATION_ID, successor_id, expected_thread_id=parent.thread_id)
    async with short_session(interaction_sessions) as database:
        thread = await database.get(ThreadRecord, parent.thread_id)
        successor = await database.get(RunRecord, successor_id)
        entry = await database.get(ThreadInboxRecord, result.id)
        thread = await database.get(ThreadRecord, parent.thread_id)
        assert thread is not None and successor is not None and entry is not None
        accepted = successor.to_resource()
        assert accepted.labels == {"team": "latest"}
        assert (thread.version, thread.current_run_id, thread.head_run_id) == (
            3,
            successor_id,
            completed_parent.id,
        )
        assert (
            accepted.parent_run_id,
            accepted.input_kind,
            accepted.trigger_type,
            accepted.trigger_entity_type,
            accepted.trigger_entity_id,
        ) == (
            completed_parent.id,
            RunInputKind.async_subagent_result,
            "async_subagent_result",
            "thread_inbox",
            result.id,
        )
        assert accepted.native_tool_contexts == completed_parent.native_tool_contexts
        assert accepted.authority_principal == completed_parent.authority_principal
        assert accepted.input == result.payload
        assert "newly available asynchronous subagent result" in project_accepted_async_subagent_result(accepted)
        assert accepted.effective_agent_config_digest == completed_parent.effective_agent_config_digest
        assert accepted.connection_selections == completed_parent.connection_selections
        assert accepted.connection_selections == completed_parent.connection_selections
        assert successor_state.envelope.checkpoint_seq == 0
        assert (
            entry.status,
            entry.target_run_id,
            entry.consumed_by_run_id,
            entry.consumed_state_digest_sha256,
            entry.consumed_checkpoint_seq,
        ) == (
            "consumed",
            successor_id,
            successor_id,
            successor_state.digest_sha256,
            0,
        )
        assert (thread.next_delivery_sequence, thread.pending_count, thread.pending_bytes) == (2, 0, 0)


async def test_object_backed_result_item_is_revalidated_for_automatic_successor(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    redis_client: Redis,
) -> None:
    states, parent, authority, child_run_id = await _accept_child(
        interaction_sessions,
        interaction_object_store,
    )
    await _seal_parent(
        interaction_sessions,
        interaction_object_store,
        states,
        parent,
        authority,
        outcome="completed",
    )
    replays, projector, output = await _complete_object_backed_child(
        interaction_sessions,
        interaction_object_store,
        redis_client,
        states,
        child_run_id,
    )
    await _project_all_lifecycle(projector)
    snapshot = await replays.read(ORGANIZATION_ID, child_run_id)
    entry = await AsyncSubagentResultPublisher(
        interaction_sessions,
        replays,
        entry_id_factory=lambda: "inb_2323232323232323",
        clock=lambda: NOW + timedelta(seconds=6),
    ).publish(organization_id=ORGANIZATION_ID, child_run_id=child_run_id)

    receipt = await AsyncSubagentSuccessorReconciler(
        interaction_sessions,
        states,
        replays,
        run_id_factory=lambda _organization, _entry, _parent: "run_2424242424242424",
        clock=lambda: NOW + timedelta(seconds=7),
        lifecycle=test_lifecycle_writer(),
    ).reconcile_thread(organization_id=ORGANIZATION_ID, thread_id=parent.thread_id)

    assert receipt.outcome == "run_accepted" and receipt.successor is not None
    payload = AsyncSubagentResultInboxPayload.model_validate(entry.payload)
    assert payload.terminal_result_item_id == snapshot.items[-1].id
    async with short_session(interaction_sessions) as database:
        successor = await database.get(RunRecord, receipt.successor.run_id)
        assert successor is not None
        accepted = successor.to_resource()
    assert accepted.input == payload.as_json()
    projected = project_accepted_async_subagent_result(accepted)
    assert snapshot.items[-1].id in projected
    assert output not in projected


async def test_oldest_result_accepts_successor_and_later_result_binds_in_fifo_order(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states, parent, authority, first_child_run_id = await _accept_child(
        interaction_sessions,
        interaction_object_store,
    )
    second_child_run_id = await _accept_another_child(
        interaction_sessions,
        interaction_object_store,
        states,
        parent,
        authority,
    )
    await _seal_parent(
        interaction_sessions,
        interaction_object_store,
        states,
        parent,
        authority,
        outcome="completed",
    )
    await _fail_child(interaction_sessions, first_child_run_id)
    await _fail_child(interaction_sessions, second_child_run_id)
    first = await AsyncSubagentResultPublisher(
        interaction_sessions,
        RunReplayStore(interaction_object_store),
        entry_id_factory=lambda: "inb_1212121212121212",
        clock=lambda: NOW + timedelta(seconds=5),
    ).publish(organization_id=ORGANIZATION_ID, child_run_id=first_child_run_id)
    second = await AsyncSubagentResultPublisher(
        interaction_sessions,
        RunReplayStore(interaction_object_store),
        entry_id_factory=lambda: "inb_1313131313131313",
        clock=lambda: NOW + timedelta(seconds=6),
    ).publish(organization_id=ORGANIZATION_ID, child_run_id=second_child_run_id)

    receipt = await AsyncSubagentSuccessorReconciler(
        interaction_sessions,
        states,
        RunReplayStore(interaction_object_store),
        run_id_factory=lambda _organization, _entry, _parent: "run_1414141414141414",
        clock=lambda: NOW + timedelta(seconds=7),
        lifecycle=test_lifecycle_writer(),
    ).reconcile_thread(organization_id=ORGANIZATION_ID, thread_id=parent.thread_id)

    assert receipt.outcome == "run_accepted" and receipt.successor is not None
    async with short_session(interaction_sessions) as database:
        rows = tuple(
            (
                await database.scalars(
                    select(ThreadInboxRecord)
                    .where(ThreadInboxRecord.id.in_((first.id, second.id)))
                    .order_by(ThreadInboxRecord.delivery_sequence)
                )
            ).all()
        )
        thread = await database.get(ThreadRecord, parent.thread_id)
        assert [(row.id, row.status, row.target_run_id) for row in rows] == [
            (first.id, "consumed", receipt.successor.run_id),
            (second.id, "pending", receipt.successor.run_id),
        ]
        assert thread is not None and (thread.next_delivery_sequence, thread.pending_count) == (3, 1)


async def test_automatic_successor_rejects_payload_forged_after_publication(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states, parent, authority, child_run_id = await _accept_child(
        interaction_sessions,
        interaction_object_store,
    )
    await _seal_parent(
        interaction_sessions,
        interaction_object_store,
        states,
        parent,
        authority,
        outcome="completed",
    )
    await _fail_child(interaction_sessions, child_run_id)
    result = await AsyncSubagentResultPublisher(
        interaction_sessions,
        RunReplayStore(interaction_object_store),
        entry_id_factory=lambda: "inb_1818181818181818",
        clock=lambda: NOW + timedelta(seconds=5),
    ).publish(organization_id=ORGANIZATION_ID, child_run_id=child_run_id)
    async with transaction(interaction_sessions) as database:
        entry = await database.get(ThreadInboxRecord, result.id)
        assert entry is not None and isinstance(entry.payload_json, dict)
        entry.payload_json = {**entry.payload_json, "result_digest": "0" * 64}

    with pytest.raises(AsyncSubagentResultError, match="sealed child outcome"):
        await AsyncSubagentSuccessorReconciler(
            interaction_sessions,
            states,
            RunReplayStore(interaction_object_store),
            run_id_factory=lambda _organization, _entry, _parent: "run_1818181818181818",
            clock=lambda: NOW + timedelta(seconds=6),
            lifecycle=test_lifecycle_writer(),
        ).reconcile_thread(organization_id=ORGANIZATION_ID, thread_id=parent.thread_id)

    async with short_session(interaction_sessions) as database:
        entry = await database.get(ThreadInboxRecord, result.id)
        successor = await database.get(RunRecord, "run_1818181818181818")
        assert entry is not None and entry.status == "pending"
        assert successor is None


async def test_concurrent_postgresql_successor_reconciliation_accepts_one_run(
    postgres_interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    sessions = postgres_interaction_sessions
    states, parent, authority, child_run_id = await _accept_child(sessions, interaction_object_store)
    await _seal_parent(
        sessions,
        interaction_object_store,
        states,
        parent,
        authority,
        outcome="completed",
    )
    await _fail_child(sessions, child_run_id)
    result = await AsyncSubagentResultPublisher(
        sessions,
        RunReplayStore(interaction_object_store),
        entry_id_factory=lambda: "inb_ffffffffffffffff",
        clock=lambda: NOW + timedelta(seconds=5),
    ).publish(organization_id=ORGANIZATION_ID, child_run_id=child_run_id)
    reconciler = AsyncSubagentSuccessorReconciler(
        sessions,
        states,
        RunReplayStore(interaction_object_store),
        run_id_factory=lambda _organization, _entry, _parent: "run_ffffffffffffffff",
        clock=lambda: NOW + timedelta(seconds=6),
        lifecycle=test_lifecycle_writer(),
    )

    receipts = await asyncio.gather(
        reconciler.reconcile_thread(organization_id=ORGANIZATION_ID, thread_id=parent.thread_id),
        reconciler.reconcile_thread(organization_id=ORGANIZATION_ID, thread_id=parent.thread_id),
    )

    assert sorted(receipt.outcome for receipt in receipts) == ["idle", "run_accepted"]
    async with short_session(sessions) as database:
        entry = await database.get(ThreadInboxRecord, result.id)
        async_runs = tuple(
            (
                await database.scalars(
                    select(RunRecord).where(RunRecord.input_kind == RunInputKind.async_subagent_result.value)
                )
            ).all()
        )
        assert entry is not None and entry.status == "consumed"
        assert [run.id for run in async_runs] == ["run_ffffffffffffffff"]


async def _accept_another_child(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
    states: RunStateStore,
    parent: Run,
    authority,
) -> str:
    parent_state = await states.read(ORGANIZATION_ID, parent.id)
    child_config = effective_agent_config()
    prepared = prepare_child_run(
        parent_run=parent,
        parent_state=parent_state.envelope,
        parent_run_attempt_id=authority.run_attempt_id,
        parent_run_attempt_fence=authority.attempt_number,
        parent_agent_instance_id="agent-parent",
        subagent_name="researcher",
        delegated_input='{"delegated_task":"second"}',
        child_definition_id=CHILD_DEFINITION_ID,
        child_agent_id=CHILD_AGENT_ID,
        child_agent_revision_id=CHILD_REVISION_ID,
        child_effective_config=child_config,
        connection_selections=(),
        child_thread_id="thread-12121212121212121212121212121212",
        child_run_id="run_1212121212121212",
        relationship_id="crr_1212121212121212",
        execution_budget=parent.execution_budget,
        created_at=NOW + timedelta(seconds=2),
    )
    accepted = await ChildRunAcceptanceService(
        sessions,
        states,
        RunPayloadStore(objects),
        clock=lambda: NOW + timedelta(seconds=2),
        lifecycle=test_lifecycle_writer(),
    ).accept(prepared, authority)
    return accepted.child_run_id


async def _seal_parent(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
    states: RunStateStore,
    parent: Run,
    authority,
    *,
    outcome: str,
) -> Run:
    execution = AttemptExecutionService(
        sessions, clock=lambda: NOW + timedelta(seconds=3), lifecycle=test_lifecycle_writer()
    )
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)

    await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id=f"parent-{outcome}",
    )

    current = await states.read(ORGANIZATION_ID, parent.id)
    candidate = (
        _completed_state(current.envelope, authority.run_attempt_id, authority.attempt_number)
        if outcome == "completed"
        else _waiting_state(current.envelope, authority.run_attempt_id, authority.attempt_number)
    )
    stored = await execution.publish_checkpoint(authority, states, current, candidate)
    receipt = await RunOutcomeService(
        sessions, RunPayloadStore(objects), clock=lambda: NOW + timedelta(seconds=4), lifecycle=test_lifecycle_writer()
    ).commit_state_outcome(authority, stored)
    assert receipt.run_status.value == outcome
    async with short_session(sessions) as database:
        row = await database.get(RunRecord, parent.id)
        assert row is not None
        return row.to_resource()


async def test_periodic_recovery_expires_bound_result_and_releases_capacity(
    interaction_sessions,
    interaction_object_store,
):
    states, parent, _, child_run_id = await _accept_child(interaction_sessions, interaction_object_store)
    await _fail_child(interaction_sessions, child_run_id)
    replays = RunReplayStore(interaction_object_store)
    result = await AsyncSubagentResultPublisher(
        interaction_sessions, replays, clock=lambda: NOW + timedelta(seconds=5)
    ).publish(organization_id=ORGANIZATION_ID, child_run_id=child_run_id)
    async with transaction(interaction_sessions) as database:
        entry = await database.get(ThreadInboxRecord, result.id)
        assert entry.target_run_id == parent.id
        entry.expires_at = NOW + timedelta(seconds=6)
    reconciler = AsyncSubagentSuccessorReconciler(
        interaction_sessions,
        states,
        replays,
        clock=lambda: NOW + timedelta(seconds=7),
        lifecycle=test_lifecycle_writer(),
    )
    assert (await reconciler.scan()).completed == 1
    assert (await reconciler.scan()).completed == 0
    async with short_session(interaction_sessions) as database:
        entry = await database.get(ThreadInboxRecord, result.id)
        thread = await database.get(ThreadRecord, parent.thread_id)
        assert entry.status == "expired" and entry.target_run_id is None
        assert (thread.pending_count, thread.pending_bytes) == (0, 0)
        assert (await database.get(ThreadRecord, parent.thread_id)).current_run_id == parent.id


async def test_completed_inline_hook_collects_only_after_delivery_retention_ends(
    interaction_sessions,
    interaction_object_store,
):
    from a13n_service.durable_operations.outbox import claim_outbox, complete_outbox
    from a13n_service.hooks.domain import InlineHookSubscriptionInput, WebhookDestinationConfig
    from a13n_service.hooks.models import HookSubscriptionRecord, HookSubscriptionRevisionRecord
    from a13n_service.hooks.persistence import create_inline_hook_subscription
    from a13n_service.hooks.retention import HookRetention
    from a13n_service.lifecycle.retention import LifecycleRetentionReconciler
    from a13n_service.secrets.models import SecretRecord

    from .conftest import USER_ID, WORKSPACE_ID

    states, parent, authority, _ = await _accept_child(interaction_sessions, interaction_object_store)
    async with transaction(interaction_sessions) as database:
        database.add(
            SecretRecord(
                id="sec_7777777777777777",
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                owner_type="workspace",
                owner_id=WORKSPACE_ID,
                key="hook",
                version=1,
                ciphertext=b"encrypted",
                nonce=b"0" * 12,
                encryption_key_id="test-key",
                created_at=NOW,
                value_updated_at=NOW,
            )
        )
        await database.flush()
        head = await create_inline_hook_subscription(
            database,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            session_id=parent.session_id,
            thread_id=parent.thread_id,
            run_id=parent.id,
            actor_type="user",
            actor_id=USER_ID,
            now=NOW,
            subscription=InlineHookSubscriptionInput(
                hook_names=("run.completed",),
                webhook=WebhookDestinationConfig(
                    endpoint_url="https://example.com/hook", signing_secret_id="sec_7777777777777777"
                ),
            ),
        )
        head_id, revision_id = head.id, head.current_revision_id
    await _seal_parent(interaction_sessions, interaction_object_store, states, parent, authority, outcome="completed")
    async with short_session(interaction_sessions) as database:
        assert (await database.get(HookSubscriptionRecord, head_id)).expired_at is not None
    assert (
        await HookRetention(interaction_sessions, minimum_age=timedelta(days=1), batch_limit=10).scan()
    ).completed == 0
    async with transaction(interaction_sessions) as database:
        claims = await claim_outbox(
            database,
            source_kind="lifecycle_event",
            destination_kind="webhook",
            now=NOW + timedelta(days=1),
            lease_duration=timedelta(seconds=30),
            limit=10,
        )
        assert len(claims) == 1
        assert await complete_outbox(database, claims[0], completed_at=NOW + timedelta(days=1))
    await LifecycleRetentionReconciler(
        interaction_sessions,
        event_horizon=timedelta(days=1),
        published_delivery_horizon=timedelta(days=1),
        dead_letter_horizon=timedelta(days=1),
        poll_interval_seconds=1,
        batch_limit=10,
        clock=lambda: NOW + timedelta(days=3),
    ).reconcile_once()
    assert (
        await HookRetention(interaction_sessions, minimum_age=timedelta(days=1), batch_limit=10).scan()
    ).completed == 2
    async with short_session(interaction_sessions) as database:
        assert await database.get(HookSubscriptionRecord, head_id) is None
        assert await database.get(HookSubscriptionRevisionRecord, revision_id) is None
        assert await database.get(RunRecord, parent.id) is not None
