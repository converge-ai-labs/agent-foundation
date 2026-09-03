from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_service.interactions import (
    AttemptExecutionService,
    AttemptPreparationAccepted,
    RunOutcomeService,
    RunPayloadStore,
    RunStateStore,
)
from a13n_service.interactions.control_models import ThreadInboxCounterRecord, ThreadInboxRecord
from a13n_service.interactions.domain import Run, RunInputKind
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.storage import ObjectStore, short_session, transaction
from a13n_service.subagents import (
    AsyncSubagentResultError,
    AsyncSubagentResultPublisher,
    AsyncSubagentSuccessorReconciler,
    ChildRunAcceptanceService,
    prepare_child_run,
    project_accepted_async_subagent_result,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, TENANT_ID, effective_agent_config
from .test_attempt_execution import _completed_state, _waiting_state
from .test_subagent_acceptance import CHILD_AGENT_ID, CHILD_DEFINITION_ID, CHILD_REVISION_ID
from .test_subagent_results import _accept_child, _fail_child

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
        entry_id_factory=lambda: "inb_bbbbbbbbbbbbbbbb",
        clock=lambda: NOW + timedelta(seconds=5),
    ).publish(tenant_id=TENANT_ID, child_run_id=child_run_id)
    assert result.target_run_id is None and result.source_waiting_run_id is None

    receipt = await AsyncSubagentSuccessorReconciler(
        interaction_sessions,
        states,
        run_id_factory=lambda _tenant, _entry, _parent: "run_bbbbbbbbbbbbbbbb",
        clock=lambda: NOW + timedelta(seconds=6),
    ).reconcile_thread(tenant_id=TENANT_ID, thread_id=parent.thread_id)

    assert receipt.outcome == "run_accepted"
    assert receipt.successor is not None
    successor_id = receipt.successor.run_id
    successor_state = await states.read(TENANT_ID, successor_id, expected_thread_id=parent.thread_id)
    async with short_session(interaction_sessions) as database:
        thread = await database.get(ThreadRecord, parent.thread_id)
        successor = await database.get(RunRecord, successor_id)
        entry = await database.get(ThreadInboxRecord, result.id)
        counter = await database.get(ThreadInboxCounterRecord, parent.thread_id)
        assert thread is not None and successor is not None and entry is not None and counter is not None
        accepted = successor.to_resource()
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
        assert accepted.authority_principal == completed_parent.authority_principal
        assert accepted.input == result.payload
        assert "newly available asynchronous subagent result" in project_accepted_async_subagent_result(accepted)
        assert accepted.effective_agent_config_digest == completed_parent.effective_agent_config_digest
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
        assert (counter.next_delivery_sequence, counter.pending_count, counter.pending_bytes) == (2, 0, 0)


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
        entry_id_factory=lambda: "inb_1212121212121212",
        clock=lambda: NOW + timedelta(seconds=5),
    ).publish(tenant_id=TENANT_ID, child_run_id=first_child_run_id)
    second = await AsyncSubagentResultPublisher(
        interaction_sessions,
        entry_id_factory=lambda: "inb_1313131313131313",
        clock=lambda: NOW + timedelta(seconds=6),
    ).publish(tenant_id=TENANT_ID, child_run_id=second_child_run_id)

    receipt = await AsyncSubagentSuccessorReconciler(
        interaction_sessions,
        states,
        run_id_factory=lambda _tenant, _entry, _parent: "run_1414141414141414",
        clock=lambda: NOW + timedelta(seconds=7),
    ).reconcile_thread(tenant_id=TENANT_ID, thread_id=parent.thread_id)

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
        counter = await database.get(ThreadInboxCounterRecord, parent.thread_id)
        assert [(row.id, row.status, row.target_run_id) for row in rows] == [
            (first.id, "consumed", receipt.successor.run_id),
            (second.id, "pending", receipt.successor.run_id),
        ]
        assert counter is not None and (counter.next_delivery_sequence, counter.pending_count) == (3, 1)


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
        entry_id_factory=lambda: "inb_1818181818181818",
        clock=lambda: NOW + timedelta(seconds=5),
    ).publish(tenant_id=TENANT_ID, child_run_id=child_run_id)
    async with transaction(interaction_sessions) as database:
        entry = await database.get(ThreadInboxRecord, result.id)
        assert entry is not None and isinstance(entry.payload_json, dict)
        entry.payload_json = {**entry.payload_json, "result_digest": "0" * 64}

    with pytest.raises(AsyncSubagentResultError, match="sealed child outcome"):
        await AsyncSubagentSuccessorReconciler(
            interaction_sessions,
            states,
            run_id_factory=lambda _tenant, _entry, _parent: "run_1818181818181818",
            clock=lambda: NOW + timedelta(seconds=6),
        ).reconcile_thread(tenant_id=TENANT_ID, thread_id=parent.thread_id)

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
        entry_id_factory=lambda: "inb_ffffffffffffffff",
        clock=lambda: NOW + timedelta(seconds=5),
    ).publish(tenant_id=TENANT_ID, child_run_id=child_run_id)
    reconciler = AsyncSubagentSuccessorReconciler(
        sessions,
        states,
        run_id_factory=lambda _tenant, _entry, _parent: "run_ffffffffffffffff",
        clock=lambda: NOW + timedelta(seconds=6),
    )

    receipts = await asyncio.gather(
        reconciler.reconcile_thread(tenant_id=TENANT_ID, thread_id=parent.thread_id),
        reconciler.reconcile_thread(tenant_id=TENANT_ID, thread_id=parent.thread_id),
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
    parent_state = await states.read(TENANT_ID, parent.id)
    child_config = effective_agent_config()
    prepared = prepare_child_run(
        parent_run=parent,
        parent_state=parent_state.envelope,
        parent_run_attempt_id=authority.run_attempt_id,
        parent_run_attempt_generation=authority.fence,
        parent_agent_instance_id="agent-parent",
        spawn_operation_id="delegate-call-2",
        subagent_name="researcher",
        delegated_input='{"delegated_task":"second"}',
        child_definition_id=CHILD_DEFINITION_ID,
        child_agent_id=CHILD_AGENT_ID,
        child_agent_revision_id=CHILD_REVISION_ID,
        child_effective_config=child_config,
        child_thread_id="thread-12121212121212121212121212121212",
        child_run_id="run_1212121212121212",
        relationship_id="crr_1212121212121212",
        mcp_tool_snapshot=parent.mcp_tool_snapshot,
        recovery_budget=parent.recovery_budget,
        created_at=NOW + timedelta(seconds=2),
    )
    accepted = await ChildRunAcceptanceService(
        sessions,
        states,
        RunPayloadStore(objects),
        clock=lambda: NOW + timedelta(seconds=2),
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
    execution = AttemptExecutionService(sessions, clock=lambda: NOW + timedelta(seconds=3))
    preparation = await execution.commit_preparation_success(authority)
    assert isinstance(preparation, AttemptPreparationAccepted)
    authority = replace(
        authority,
        expected_run_version=preparation.mutation.run_version,
        expected_attempt_version=preparation.mutation.attempt_version,
    )
    entered = await execution.enter_harness(
        authority,
        preparation=preparation,
        harness_run_id=f"parent-{outcome}",
    )
    authority = replace(
        authority,
        expected_run_version=entered.run_version,
        expected_attempt_version=entered.attempt_version,
    )
    current = await states.read(TENANT_ID, parent.id)
    candidate = (
        _completed_state(current.envelope, authority.run_attempt_id, authority.fence)
        if outcome == "completed"
        else _waiting_state(current.envelope, authority.run_attempt_id, authority.fence)
    )
    stored = await execution.publish_checkpoint(authority, states, current, candidate)
    receipt = await RunOutcomeService(
        sessions,
        RunPayloadStore(objects),
        clock=lambda: NOW + timedelta(seconds=4),
    ).commit_state_outcome(authority, stored, expected_thread_version=1)
    assert receipt.run_status.value == outcome
    async with short_session(sessions) as database:
        row = await database.get(RunRecord, parent.id)
        assert row is not None
        return row.to_resource()
