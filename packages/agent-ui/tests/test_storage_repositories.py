from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_environment_provider import (
    EnvironmentProviderErrorCategory,
    EnvironmentProviderErrorContext,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentProviderRecoveryHint,
    EnvironmentProviderSafeError,
    EnvironmentState,
)
from a13n_harness import HarnessState
from a13n_ui.errors import StoreConflictError, StoreIntegrityError
from a13n_ui.settings import StorageSettings
from a13n_ui.storage import (
    CompactChildDisplay,
    EnvironmentBindingKey,
    ObjectKind,
    SafeFailure,
    SnapshotRef,
    StoredChildCheckpoint,
    StoredEnvironmentState,
    StoredSessionContinuation,
    open_local_store,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests

pytestmark = pytest.mark.anyio

_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


async def _create_session(root: Path):
    settings = StorageSettings(data_root=root)
    context = open_local_store(settings)
    store = await context.__aenter__()

    agent = await store.objects.publish(
        object_kind=ObjectKind.agent_snapshot,
        object_schema_version="1",
        payload={"agent": "root"},
        created_at=_NOW,
    )
    environment = await store.objects.publish(
        object_kind=ObjectKind.environment_snapshot,
        object_schema_version="1",
        payload={"kind": "native"},
        created_at=_NOW,
    )
    await store.configurations.accept(
        source_digest="1" * 64,
        yaml_digest="2" * 64,
        document={"schema_version": "1"},
        snapshots={
            ("agent", "root"): agent.ref,
            ("environment", "native"): environment.ref,
        },
        restart_required=False,
        expected_current_digest=None,
        accepted_at=_NOW,
    )

    root_state = HarnessState.new()
    continuation_value = StoredSessionContinuation(
        harness_release="test-harness",
        harness_state=root_state,
        created_at=_NOW,
    )
    continuation = await store.objects.publish_model(
        object_kind=ObjectKind.session_continuation,
        value=continuation_value,
    )
    session = await store.sessions.create(
        session_id="session-test",
        root_thread_id=root_state.thread_id,
        agent_snapshot=SnapshotRef(snapshot_kind="agent", object=agent.ref),
        environment_snapshot=SnapshotRef(snapshot_kind="environment", object=environment.ref),
        continuation=continuation.ref,
        created_at=_NOW,
    )
    return context, store, session, continuation_value


async def test_session_continuation_round_trips_native_deferred_requests(tmp_path: Path) -> None:
    context, store, _session, baseline = await _create_session(tmp_path)
    try:
        deferred = DeferredToolRequests(
            calls=[
                ToolCallPart(
                    tool_name="lookup",
                    args={"query": "agent state"},
                    tool_call_id="call-1",
                )
            ],
            metadata={"call-1": {"origin": "test"}},
        )
        value = baseline.model_copy(update={"deferred_requests": deferred})
        published = await store.objects.publish_model(
            object_kind=ObjectKind.session_continuation,
            value=value,
        )

        restored = await store.objects.read_model(published.ref, StoredSessionContinuation)
        assert restored == value
        assert restored.deferred_requests == deferred
        assert restored.deferred_requests is not None
        assert restored.deferred_requests.calls[0].tool_call_id == "call-1"

        empty_value = value.model_copy(update={"deferred_requests": DeferredToolRequests()})
        empty = await store.objects.publish_model(
            object_kind=ObjectKind.session_continuation,
            value=empty_value,
        )
        assert await store.objects.read_model(empty.ref, StoredSessionContinuation) == empty_value
    finally:
        await context.__aexit__(None, None, None)


async def test_root_continuation_selection_is_optimistic(tmp_path: Path) -> None:
    context, store, session, baseline = await _create_session(tmp_path)
    try:
        next_value = baseline.model_copy(update={"created_at": datetime(2026, 9, 1, 12, 1, tzinfo=UTC)})
        next_object = await store.objects.publish_model(
            object_kind=ObjectKind.session_continuation,
            value=next_value,
        )
        stale_value = baseline.model_copy(update={"created_at": datetime(2026, 9, 1, 12, 2, tzinfo=UTC)})
        stale_object = await store.objects.publish_model(
            object_kind=ObjectKind.session_continuation,
            value=stale_value,
        )

        selected = await store.sessions.select_continuation(
            session_id=session.session_id,
            expected=session.continuation,
            replacement=next_object.ref,
        )
        assert selected.continuation == next_object.ref

        with pytest.raises(StoreConflictError) as conflict:
            await store.sessions.select_continuation(
                session_id=session.session_id,
                expected=session.continuation,
                replacement=stale_object.ref,
            )
        assert conflict.value.code == "session_continuation_conflict"
        retained = await store.sessions.get(session.session_id)
        assert retained is not None
        assert retained.continuation == next_object.ref
        assert await store.objects.read_model(next_object.ref, StoredSessionContinuation) == next_value
    finally:
        await context.__aexit__(None, None, None)


async def test_environment_state_uses_complete_key_and_compare_select(tmp_path: Path) -> None:
    context, store, session, _baseline = await _create_session(tmp_path)
    try:
        key = EnvironmentBindingKey(
            session_id=session.session_id,
            profile_digest=session.environment_snapshot.object.logical_digest,
            binder_key="vendor.workspace-binder",
            normalized_folder=str(tmp_path.resolve()),
        )
        head = await store.environment_states.ensure(key, created_at=_NOW)
        assert head.state is None

        first_value = StoredEnvironmentState(
            binding=key,
            provider_schema_version="1",
            state=EnvironmentState(provider_key="vendor.provider", state_version="1", state={"target": "one"}),
            created_at=_NOW,
        )
        first = await store.objects.publish_model(
            object_kind=ObjectKind.environment_state,
            value=first_value,
        )
        second_value = first_value.model_copy(
            update={
                "state": EnvironmentState(
                    provider_key="vendor.provider",
                    state_version="1",
                    state={"target": "two"},
                ),
                "created_at": datetime(2026, 9, 1, 12, 1, tzinfo=UTC),
            }
        )
        second = await store.objects.publish_model(
            object_kind=ObjectKind.environment_state,
            value=second_value,
        )

        selected = await store.environment_states.select_state(
            key=key,
            expected=None,
            replacement=first.ref,
        )
        assert selected.state == first.ref
        with pytest.raises(StoreConflictError) as conflict:
            await store.environment_states.select_state(
                key=key,
                expected=None,
                replacement=second.ref,
            )
        assert conflict.value.code == "environment_state_conflict"
        retained = await store.environment_states.get(key)
        assert retained is not None
        assert retained.state == first.ref

        cleanup_failure = EnvironmentProviderSafeError(
            code="cleanup_failed",
            category=EnvironmentProviderErrorCategory.CLEANUP,
            certainty=EnvironmentProviderOutcomeCertainty.KNOWN,
            message="The test resource could not be removed.",
            recovery_hint=EnvironmentProviderRecoveryHint.RECONCILE,
            context=EnvironmentProviderErrorContext(
                provider_key="vendor.provider",
                action="delete",
            ),
        )
        cleanup = await store.environment_states.set_cleanup(
            key=key,
            status="failed",
            failure=cleanup_failure,
        )
        assert cleanup.cleanup_failure == cleanup_failure
        retained_cleanup = await store.environment_states.get(key)
        assert retained_cleanup is not None
        assert retained_cleanup.cleanup_failure == cleanup_failure

        other_key = key.model_copy(update={"normalized_folder": str((tmp_path / "other").resolve())})
        other = await store.environment_states.ensure(other_key)
        assert other.state is None
    finally:
        await context.__aexit__(None, None, None)


async def test_child_segments_are_contiguous_and_terminal_persistence_fails_closed(tmp_path: Path) -> None:
    context, store, session, _baseline = await _create_session(tmp_path)
    try:
        child_state = HarnessState.new()
        execution = await store.child_executions.create(
            execution_id="execution-0",
            session_id=session.session_id,
            parent_thread_id=session.root_thread_id,
            child_thread_id=child_state.thread_id,
            child_run_id="run-0",
            subagent_name="reviewer",
            child_definition_id=f"agent-ui:{'3' * 64}",
            child_definition_digest="3" * 64,
            input="Review the change",
            owner_process_generation=store.process_generation,
            created_at=_NOW,
        )
        assert execution.segment_index == 0
        assert execution.resumed_from is None

        progress_value = StoredChildCheckpoint(
            harness_release="test-harness",
            execution_id=execution.execution_id,
            child_thread_id=execution.child_thread_id,
            child_run_id="run-0",
            segment_index=0,
            harness_state=child_state,
            display=CompactChildDisplay(),
            terminal=False,
            created_at=_NOW,
        )
        progress = await store.objects.publish_model(
            object_kind=ObjectKind.child_checkpoint,
            value=progress_value,
        )
        progress_head = await store.child_executions.select_checkpoint(
            execution_id=execution.execution_id,
            expected=None,
            checkpoint=progress.ref,
            child_run_id="run-0",
        )
        assert progress_head.status == "running"
        assert not progress_head.resumable

        failure = SafeFailure(code="checkpoint_publish_failed", message="Terminal checkpoint could not be saved.")
        failed = await store.child_executions.fail_terminal_persistence(
            execution_id=execution.execution_id,
            failure=failure,
        )
        assert failed.status == "failed"
        assert failed.selected_checkpoint == progress.ref
        assert not failed.selected_checkpoint_terminal
        assert not failed.resumable
        with pytest.raises(StoreIntegrityError) as not_resumable:
            await store.child_executions.resume(
                previous_execution_id=execution.execution_id,
                execution_id="execution-1",
                child_run_id="run-1",
                child_definition_digest="3" * 64,
                input="Continue the review",
                owner_process_generation=store.process_generation,
            )
        assert not_resumable.value.code == "child_execution_not_resumable"

        resumable_state = HarnessState.new()
        resumable = await store.child_executions.create(
            execution_id="execution-a",
            session_id=session.session_id,
            parent_thread_id=session.root_thread_id,
            child_thread_id=resumable_state.thread_id,
            child_run_id="run-a",
            subagent_name="researcher",
            child_definition_id=f"agent-ui:{'4' * 64}",
            child_definition_digest="4" * 64,
            input="Research the topic",
            owner_process_generation=store.process_generation,
        )
        terminal_value = StoredChildCheckpoint(
            harness_release="test-harness",
            execution_id=resumable.execution_id,
            child_thread_id=resumable.child_thread_id,
            child_run_id="run-a",
            segment_index=0,
            harness_state=resumable_state,
            display=CompactChildDisplay(final_answer="done"),
            terminal=True,
            created_at=_NOW,
        )
        terminal = await store.objects.publish_model(
            object_kind=ObjectKind.child_checkpoint,
            value=terminal_value,
        )
        succeeded = await store.child_executions.select_checkpoint(
            execution_id=resumable.execution_id,
            expected=None,
            checkpoint=terminal.ref,
            child_run_id="run-a",
            terminal_status="succeeded",
            resumable=True,
        )
        assert succeeded.selected_checkpoint_terminal
        assert succeeded.resumable

        linked = await store.child_executions.resume(
            previous_execution_id=succeeded.execution_id,
            execution_id="execution-b",
            child_run_id="run-b",
            child_definition_digest=succeeded.child_definition_digest,
            input="Continue the research",
            owner_process_generation=store.process_generation,
        )
        assert linked.child_thread_id == succeeded.child_thread_id
        assert linked.segment_index == 1
        assert linked.resumed_from == succeeded.execution_id
    finally:
        await context.__aexit__(None, None, None)


async def test_store_reconciles_only_running_children_with_confirmed_dead_owners(tmp_path: Path) -> None:
    context, store, session, _baseline = await _create_session(tmp_path)
    execution = await store.child_executions.create(
        execution_id="execution-crashed",
        session_id=session.session_id,
        parent_thread_id=session.root_thread_id,
        child_thread_id=HarnessState.new().thread_id,
        child_run_id="run-crashed",
        subagent_name="reviewer",
        child_definition_id=f"agent-ui:{'5' * 64}",
        child_definition_digest="5" * 64,
        input="Inspect after a crash",
        owner_process_generation=store.process_generation,
    )

    concurrent_context = open_local_store(StorageSettings(data_root=tmp_path))
    concurrent = await concurrent_context.__aenter__()
    try:
        still_running = await concurrent.child_executions.get(execution.execution_id)
        assert still_running is not None
        assert still_running.status == "running"
    finally:
        await concurrent_context.__aexit__(None, None, None)

    await context.__aexit__(None, None, None)

    recovered_context = open_local_store(StorageSettings(data_root=tmp_path))
    recovered = await recovered_context.__aenter__()
    try:
        lost = await recovered.child_executions.get(execution.execution_id)
        assert lost is not None
        assert lost.status == "lost"
        assert lost.completed_at is not None
    finally:
        await recovered_context.__aexit__(None, None, None)
