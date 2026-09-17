from __future__ import annotations

import pytest
from a13n_service.gateway.queries import NativeInteractionQueries, NativeQueryError
from a13n_service.http_errors import application_error_status
from a13n_service.run_stream import RunDisplayStore
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.hooks.support import RUN_ID, hook_actor, seed_hook_actor_access, seed_run_and_secret
from tests.interactions.conftest import SESSION_ID, THREAD_ID, WORKSPACE_ID

pytestmark = pytest.mark.anyio


@pytest.fixture
async def queries(lifecycle_interaction_sessions: async_sessionmaker[AsyncSession], tmp_path):
    await seed_run_and_secret(lifecycle_interaction_sessions)
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    return NativeInteractionQueries(lifecycle_interaction_sessions, RunDisplayStore(objects))


async def test_native_interaction_resource_reads_use_durable_identity(queries: NativeInteractionQueries) -> None:
    sessions = await queries.list_sessions(actor=hook_actor(), workspace_id=WORKSPACE_ID, limit=50, cursor=None)
    threads = await queries.list_threads(actor=hook_actor(), session_id=SESSION_ID, limit=50, cursor=None)
    thread = await queries.get_thread(actor=hook_actor(), thread_id=THREAD_ID)
    workspace_runs = await queries.list_runs(
        actor=hook_actor(), workspace_id=WORKSPACE_ID, thread_id=None, limit=50, cursor=None
    )
    thread_runs = await queries.list_runs(
        actor=hook_actor(), workspace_id=None, thread_id=THREAD_ID, limit=50, cursor=None
    )
    run = await queries.get_run(actor=hook_actor(), run_id=RUN_ID)
    lineage = await queries.lineage(actor=hook_actor(), run_id=RUN_ID)

    assert [item.id for item in sessions.items] == [SESSION_ID]
    assert [item.id for item in threads.items] == [THREAD_ID]
    assert thread.current_run_id == RUN_ID
    assert [item.id for item in workspace_runs.items] == [RUN_ID]
    assert [item.id for item in thread_runs.items] == [RUN_ID]
    assert run.id == RUN_ID
    assert run.model_dump(mode="json")["sealed_state_digest_sha256"] is None
    assert "request_fingerprint" not in run.model_dump()
    assert "encrypted_config_payload" not in run.model_dump()
    assert lineage.head_run_id == RUN_ID
    assert lineage.items[0].run_id == RUN_ID
    assert lineage.items[0].depth_from_head == 0


async def test_pending_and_attempt_collections_are_empty_for_new_run(queries: NativeInteractionQueries) -> None:
    pending = await queries.pending_actions(actor=hook_actor(), run_id=RUN_ID)
    attempts = await queries.list_attempts(actor=hook_actor(), run_id=RUN_ID, limit=50, cursor=None)

    assert pending.items == ()
    assert attempts.items == ()


async def test_missing_retained_items_are_explicit(queries: NativeInteractionQueries) -> None:
    with pytest.raises(NativeQueryError) as captured:
        await queries.items(actor=hook_actor(), run_id=RUN_ID, limit=50, cursor=None)

    assert captured.value.code == "items_unavailable"
    assert application_error_status(captured.value) == 409


async def test_active_display_pagination_restarts_when_snapshot_changes(queries: NativeInteractionQueries) -> None:
    from a13n_service.run_stream import RetainedItem, RunDisplaySnapshot, run_stream_key_digest_sha256

    from tests.interactions.conftest import ORGANIZATION_ID

    snapshot = RunDisplaySnapshot(
        version=1,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        cursor="3-0",
        stream_key_digest_sha256=run_stream_key_digest_sha256(ORGANIZATION_ID, RUN_ID),
        items=tuple(
            RetainedItem(
                id=f"itm_{index:016d}",
                kind="text_message",
                state="in_progress",
                first_stream_id=f"{index}-0",
                last_stream_id=f"{index}-0",
                content={"text": f"part-{index}"},
            )
            for index in (1, 2)
        ),
    )
    stored = await queries._display.publish(ORGANIZATION_ID, snapshot, previous=None)
    first = await queries.items(actor=hook_actor(), run_id=RUN_ID, limit=1, cursor=None)
    assert first.snapshot_version == 1 and first.projection_cursor == "3-0"
    assert first.complete and not first.finalized and first.incomplete_reason is None
    assert first.items[0].state == "in_progress" and first.next_cursor is not None
    second = await queries.items(actor=hook_actor(), run_id=RUN_ID, limit=1, cursor=first.next_cursor)
    assert second.snapshot_version == first.snapshot_version and second.items[0].id != first.items[0].id
    assert second.next_cursor is None
    await queries._display.publish(
        ORGANIZATION_ID, snapshot.model_copy(update={"version": 2, "cursor": "4-0"}), previous=stored
    )
    with pytest.raises(NativeQueryError) as captured:
        await queries.items(actor=hook_actor(), run_id=RUN_ID, limit=1, cursor=first.next_cursor)
    assert captured.value.code == "items_snapshot_changed" and application_error_status(captured.value) == 409
    restarted = await queries.items(actor=hook_actor(), run_id=RUN_ID, limit=10, cursor=None)
    assert restarted.snapshot_version == 2 and restarted.projection_cursor == "4-0"
