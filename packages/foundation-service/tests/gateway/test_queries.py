from __future__ import annotations

import pytest
from a13n_service.gateway.queries import NativeInteractionQueries, NativeQueryError
from a13n_service.run_stream import RunReplayStore
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
    return NativeInteractionQueries(lifecycle_interaction_sessions, RunReplayStore(objects))


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
    assert captured.value.status_code == 409
