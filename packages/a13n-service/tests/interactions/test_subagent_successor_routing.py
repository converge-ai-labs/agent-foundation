from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.control_domain import ThreadRunSubmissionIntent
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.domain import RunInputKind, RunLineageKind
from a13n_service.interactions.initialization import RunStateSeed, initialize_completed_continuation_state
from a13n_service.interactions.input import AgentInput, TextContent
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.queue import QueuedSubmissionStore
from a13n_service.run_stream import RunReplayStore
from a13n_service.storage import ObjectStore, short_session, transaction
from a13n_service.subagents import (
    AsyncSubagentResultPublisher,
    AsyncSubagentSuccessorError,
    AsyncSubagentSuccessorReconciler,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.lifecycle_support import test_lifecycle_writer
from tests.memory.selection_support import ordinary_memory

from .conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID
from .test_acceptance import _accepted_run, _inline_hooks
from .test_subagent_results import RecordingSignals, _accept_child, _fail_child
from .test_subagent_successors import _seal_parent

pytestmark = pytest.mark.anyio


async def test_unbound_result_rebinds_to_a_current_active_run_and_signals_it(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states, parent, _, child_run_id = await _accept_child(
        interaction_sessions,
        interaction_object_store,
    )
    await _fail_child(interaction_sessions, child_run_id)
    result = await AsyncSubagentResultPublisher(
        interaction_sessions,
        RunReplayStore(interaction_object_store),
        entry_id_factory=lambda: "inb_1717171717171717",
        clock=lambda: NOW + timedelta(seconds=5),
    ).publish(organization_id=ORGANIZATION_ID, child_run_id=child_run_id)
    async with transaction(interaction_sessions) as database:
        entry = await database.get(ThreadInboxRecord, result.id)
        assert entry is not None
        entry.target_run_id = None
    signals = RecordingSignals()

    receipt = await AsyncSubagentSuccessorReconciler(
        interaction_sessions,
        states,
        RunReplayStore(interaction_object_store),
        bindings=ordinary_memory(interaction_sessions),
        signals=signals,
        clock=lambda: NOW + timedelta(seconds=6),
        lifecycle=test_lifecycle_writer(),
    ).reconcile_thread(organization_id=ORGANIZATION_ID, thread_id=parent.thread_id)

    assert receipt.outcome == "bound_active"
    async with short_session(interaction_sessions) as database:
        entry = await database.get(ThreadInboxRecord, result.id)
        assert entry is not None and entry.target_run_id == parent.id
    assert signals.threads == [(ORGANIZATION_ID, parent.thread_id)]


async def test_queued_submission_keeps_precedence_over_unbound_result(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states, parent, authority, child_run_id = await _accept_child(
        interaction_sessions,
        interaction_object_store,
    )
    await QueuedSubmissionStore(
        interaction_sessions,
        _inline_hooks(),
        clock=lambda: NOW + timedelta(seconds=2),
    ).enqueue(
        organization_id=ORGANIZATION_ID,
        thread_id=parent.thread_id,
        expected_thread_version=1,
        authority_principal=parent.authority_principal,
        submission=ThreadRunSubmissionIntent(
            input=AgentInput(schema_version="1", content=(TextContent(text="queued first"),))
        ),
        queued_submission_id="qsub_bbbbbbbbbbbbbbbb",
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
        entry_id_factory=lambda: "inb_cccccccccccccccc",
        clock=lambda: NOW + timedelta(seconds=5),
    ).publish(organization_id=ORGANIZATION_ID, child_run_id=child_run_id)

    receipt = await AsyncSubagentSuccessorReconciler(
        interaction_sessions,
        states,
        RunReplayStore(interaction_object_store),
        bindings=ordinary_memory(interaction_sessions),
        clock=lambda: NOW + timedelta(seconds=6),
        lifecycle=test_lifecycle_writer(),
    ).reconcile_thread(organization_id=ORGANIZATION_ID, thread_id=parent.thread_id)

    assert receipt.outcome == "queue_precedence"
    async with short_session(interaction_sessions) as database:
        entry = await database.get(ThreadInboxRecord, result.id)
        async_runs = await database.scalar(
            select(func.count())
            .select_from(RunRecord)
            .where(RunRecord.input_kind == RunInputKind.async_subagent_result.value)
        )
        assert entry is not None
        assert (entry.status, entry.target_run_id, entry.source_waiting_run_id) == ("pending", None, None)
        assert async_runs == 0


async def test_waiting_parent_retains_result_without_creating_successor(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
) -> None:
    states, parent, authority, child_run_id = await _accept_child(
        interaction_sessions,
        interaction_object_store,
    )
    waiting_parent = await _seal_parent(
        interaction_sessions,
        interaction_object_store,
        states,
        parent,
        authority,
        outcome="waiting",
    )
    await _fail_child(interaction_sessions, child_run_id)
    result = await AsyncSubagentResultPublisher(
        interaction_sessions,
        RunReplayStore(interaction_object_store),
        entry_id_factory=lambda: "inb_dddddddddddddddd",
        clock=lambda: NOW + timedelta(seconds=5),
    ).publish(organization_id=ORGANIZATION_ID, child_run_id=child_run_id)

    assert (result.target_run_id, result.source_waiting_run_id) == (None, waiting_parent.id)
    reconciler = AsyncSubagentSuccessorReconciler(
        interaction_sessions,
        states,
        RunReplayStore(interaction_object_store),
        bindings=ordinary_memory(interaction_sessions),
        lifecycle=test_lifecycle_writer(),
    )
    assert await reconciler.reconcile_once() == 0


async def test_failed_current_uses_preserved_completed_head_as_result_parent(
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
        entry_id_factory=lambda: "inb_1515151515151515",
        clock=lambda: NOW + timedelta(seconds=5),
    ).publish(organization_id=ORGANIZATION_ID, child_run_id=child_run_id)
    completed_state = await states.read(ORGANIZATION_ID, completed_parent.id)
    manual_run_id = "run_1515151515151515"
    manual_state = initialize_completed_continuation_state(
        RunStateSeed(
            run_id=manual_run_id,
            agent_id=completed_parent.agent_id,
            agent_revision_id=completed_parent.agent_revision_id,
            effective_agent_config=completed_state.envelope.effective_agent_config,
        ),
        completed_state.envelope,
    )
    manual_run = _accepted_run(
        run_id=manual_run_id,
        thread_id=parent.thread_id,
        idempotency_key="manual-before-result",
        request_fingerprint="5" * 64,
        config=completed_state.envelope.effective_agent_config,
    ).model_copy(
        update={
            "parent_run_id": completed_parent.id,
            "lineage_kind": RunLineageKind.continue_,
        }
    )
    await RunAcceptanceService(
        interaction_sessions,
        states,
        RunPayloadStore(interaction_object_store),
        _inline_hooks(),
        bindings=ordinary_memory(interaction_sessions),
        clock=lambda: NOW + timedelta(seconds=6),
        lifecycle=test_lifecycle_writer(),
    ).advance_thread(
        run=manual_run,
        state=manual_state,
        expected_thread_version=2,
        expected_current_run_id=completed_parent.id,
        expected_head_run_id=completed_parent.id,
        next_head_run_id=completed_parent.id,
    )
    await RunOutcomeService(
        interaction_sessions,
        RunPayloadStore(interaction_object_store),
        clock=lambda: NOW + timedelta(seconds=7),
        lifecycle=test_lifecycle_writer(),
    ).cancel(
        organization_id=ORGANIZATION_ID,
        run_id=manual_run_id,
        expected_run_version=1,
        expected_thread_version=3,
        failure=SafeFailure(code="manual_cancel", message="Manual continuation cancelled."),
    )

    receipt = await AsyncSubagentSuccessorReconciler(
        interaction_sessions,
        states,
        RunReplayStore(interaction_object_store),
        bindings=ordinary_memory(interaction_sessions),
        run_id_factory=lambda _organization, _entry, _parent: "run_1616161616161616",
        clock=lambda: NOW + timedelta(seconds=8),
        lifecycle=test_lifecycle_writer(),
    ).reconcile_thread(organization_id=ORGANIZATION_ID, thread_id=parent.thread_id)

    assert receipt.outcome == "run_accepted" and receipt.successor is not None
    async with short_session(interaction_sessions) as database:
        successor = await database.get(RunRecord, receipt.successor.run_id)
        entry = await database.get(ThreadInboxRecord, result.id)
        assert successor is not None and entry is not None
        assert successor.parent_run_id == completed_parent.id
        assert successor.authority_principal_id == completed_parent.authority_principal.principal_id
        assert (entry.status, entry.consumed_by_run_id) == ("consumed", successor.id)


async def test_automatic_successor_reauthorizes_origin_principal_before_commit(
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
        entry_id_factory=lambda: "inb_eeeeeeeeeeeeeeee",
        clock=lambda: NOW + timedelta(seconds=5),
    ).publish(organization_id=ORGANIZATION_ID, child_run_id=child_run_id)
    async with transaction(interaction_sessions) as database:
        binding = await database.scalar(
            select(RoleBindingRecord).where(RoleBindingRecord.principal_id == parent.authority_principal.principal_id)
        )
        assert binding is not None
        await database.delete(binding)

    with pytest.raises(AsyncSubagentSuccessorError, match="no longer authorized"):
        await AsyncSubagentSuccessorReconciler(
            interaction_sessions,
            states,
            RunReplayStore(interaction_object_store),
            bindings=ordinary_memory(interaction_sessions),
            run_id_factory=lambda _organization, _entry, _parent: "run_eeeeeeeeeeeeeeee",
            clock=lambda: NOW + timedelta(seconds=6),
            lifecycle=test_lifecycle_writer(),
        ).reconcile_thread(organization_id=ORGANIZATION_ID, thread_id=parent.thread_id)

    async with short_session(interaction_sessions) as database:
        entry = await database.get(ThreadInboxRecord, result.id)
        successor = await database.get(RunRecord, "run_eeeeeeeeeeeeeeee")
        assert entry is not None and successor is None
        assert (entry.status, entry.target_run_id) == ("pending", None)


async def test_automatic_successor_reauthorizes_child_result_before_commit(
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
        entry_id_factory=lambda: "inb_ffffffffffffffff",
        clock=lambda: NOW + timedelta(seconds=5),
    ).publish(organization_id=ORGANIZATION_ID, child_run_id=child_run_id)
    async with transaction(interaction_sessions) as database:
        workspace_binding = await database.scalar(
            select(RoleBindingRecord).where(
                RoleBindingRecord.principal_id == parent.authority_principal.principal_id,
                RoleBindingRecord.resource_type == "workspace",
            )
        )
        assert workspace_binding is not None
        await database.delete(workspace_binding)
        database.add(
            RoleBindingRecord(
                id="rbac_ffffffffffffffff",
                organization_id=ORGANIZATION_ID,
                workspace_id=WORKSPACE_ID,
                principal_type="user",
                principal_id=USER_ID,
                resource_type="agent",
                resource_id=parent.agent_id,
                role_key="runner",
                created_by_user_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    with pytest.raises(AsyncSubagentSuccessorError, match="no longer authorized"):
        await AsyncSubagentSuccessorReconciler(
            interaction_sessions,
            states,
            RunReplayStore(interaction_object_store),
            bindings=ordinary_memory(interaction_sessions),
            run_id_factory=lambda _organization, _entry, _parent: "run_ffffffffffffffff",
            clock=lambda: NOW + timedelta(seconds=6),
            lifecycle=test_lifecycle_writer(),
        ).reconcile_thread(organization_id=ORGANIZATION_ID, thread_id=parent.thread_id)

    async with short_session(interaction_sessions) as database:
        entry = await database.get(ThreadInboxRecord, result.id)
        successor = await database.get(RunRecord, "run_ffffffffffffffff")
        assert entry is not None and successor is None
        assert (entry.status, entry.target_run_id) == ("pending", None)
