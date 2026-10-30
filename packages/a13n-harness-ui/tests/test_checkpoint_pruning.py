"""Deletion-scoped cleanup on native Linux, macOS and Windows filesystems."""

import asyncio

import pytest
from a13n_harness_ui.errors import StoreConflictError, StoreIntegrityError
from a13n_harness_ui.restart_models import RestartBatch, RestartItem
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import open_local_store
from a13n_harness_ui.storage.continuations import publish_continuation, read_continuation
from a13n_harness_ui.storage.contracts import ReferencedContinuation
from a13n_harness_ui.storage.database import short_session
from a13n_harness_ui.storage.display import seal_display
from a13n_harness_ui.storage.models import DeletedThreadRecord
from a13n_harness_ui.storage.objects import ObjectKind
from sqlalchemy import select

from .test_continuation_objects import checkpoint
from .test_thread_repository import _configuration, _initial

pytestmark = pytest.mark.anyio


async def create_owner(store, value):
    return await store.threads.create(
        thread_id=value.harness_state.thread_id, configuration=_configuration(), initial_state=_initial()
    )


async def delete_owner(store, value):
    return await store.threads.delete(
        value.harness_state.thread_id,
        expected_version=1,
        active_execution_ids=frozenset(),
        busy_thread_ids=frozenset(),
    )


async def test_only_explicitly_deleted_threads_are_reclaimed(tmp_path):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        value = checkpoint()
        archive = await seal_display(store.objects, value.display)
        value = value.model_copy(update={"display": archive})
        await create_owner(store, value)
        deleted = await publish_continuation(store.objects, value)
        # A fork owns fresh native/display payloads but shares the prefix.
        fork = value.model_copy(update={"harness_state": value.harness_state.fork()})
        await create_owner(store, fork)
        first = await publish_continuation(store.objects, fork)
        second = await publish_continuation(store.objects, fork)
        # Missing owner is not deletion evidence (including pre-upgrade leftovers).
        unknown = value.model_copy(update={"harness_state": value.harness_state.fork()})
        unknown_ref = await publish_continuation(store.objects, unknown)
        unrelated = await store.objects.publish(
            object_kind=ObjectKind.mcp_app_resource, object_schema_version="1", payload={"keep": True}
        )
        assert await store.checkpoints.prune() == 0
        await delete_owner(store, value)
        assert await store.checkpoints.prune() == 3
        refs = await store.objects.references()
        assert deleted not in refs
        assert archive.prefix in refs and unrelated.ref in refs
        for reference, expected in ((first, fork), (second, fork), (unknown_ref, unknown)):
            assert await read_continuation(store.objects, reference, include_display=True) == expected
        assert await store.checkpoints.prune() == 0


async def test_failed_deletion_has_no_marker_and_deleted_identity_cannot_be_reused(tmp_path):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        value = checkpoint()
        await create_owner(store, value)
        ref = await publish_continuation(store.objects, value)
        with pytest.raises(StoreConflictError):
            await store.threads.delete(
                value.harness_state.thread_id,
                expected_version=2,
                active_execution_ids=frozenset(),
                busy_thread_ids=frozenset(),
            )
        async with short_session(store.database.sessions) as session:
            assert not list(await session.scalars(select(DeletedThreadRecord)))
        assert await store.checkpoints.prune() == 0
        assert await read_continuation(store.objects, ref)
        await delete_owner(store, value)
        with pytest.raises(StoreIntegrityError) as failure:
            await create_owner(store, value)
        assert failure.value.code == "thread_deleted"
        source = await store.threads.create(
            thread_id="thread-source", configuration=_configuration(), initial_state=_initial()
        )
        with pytest.raises(StoreIntegrityError) as failure:
            await store.threads.fork(
                source=source,
                thread_id=value.harness_state.thread_id,
                initial_state=_initial(),
                continuation=None,
                read_model=None,
            )
        assert failure.value.code == "thread_deleted"


@pytest.mark.parametrize("state", ["ready", "consumed"])
async def test_restart_reference_protects_deleted_threads_checkpoint(tmp_path, state):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        value = checkpoint()
        await create_owner(store, value)
        ref = await publish_continuation(store.objects, value)
        await store.restarts.publish(
            RestartBatch(
                batch_id="restart-test",
                state=state,
                items=(
                    RestartItem(
                        thread_id=value.harness_state.thread_id,
                        root_thread_id=value.harness_state.thread_id,
                        run_id="run-test",
                        composition=value.run_composition,
                        checkpoint=ref,
                    ),
                ),
            )
        )
        await delete_owner(store, value)
        assert await store.checkpoints.prune() == 0
        assert await read_continuation(store.objects, ref, include_display=True) == value


@pytest.mark.parametrize("kind", [ObjectKind.continuation_display, ObjectKind.continuation])
async def test_interrupted_publication_payloads_are_out_of_scope(tmp_path, monkeypatch, kind):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        value = checkpoint()
        await create_owner(store, value)
        publish = store.objects.publish_model

        async def fail(**kwargs):
            if kwargs["object_kind"] == kind:
                raise OSError("publication unavailable")
            return await publish(**kwargs)

        monkeypatch.setattr(store.objects, "publish_model", fail)
        with pytest.raises(OSError):
            await publish_continuation(store.objects, value)
        await delete_owner(store, value)
        before = await store.objects.references()
        assert before
        assert await store.checkpoints.prune() == 0
        assert await store.objects.references() == before


@pytest.mark.parametrize(
    "kind", [ObjectKind.continuation, ObjectKind.continuation_state, ObjectKind.continuation_display]
)
async def test_unlink_failure_is_best_effort_without_generic_orphan_sweeping(tmp_path, monkeypatch, kind):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        value = checkpoint()
        await create_owner(store, value)
        ref = await publish_continuation(store.objects, value)
        manifest = await store.objects.read_model(ref, ReferencedContinuation)
        await delete_owner(store, value)
        remove = store.objects.remove

        async def fail(reference):
            if reference.object_kind == kind:
                raise PermissionError("Windows sharing violation")
            await remove(reference)

        monkeypatch.setattr(store.objects, "remove", fail)
        await store.checkpoints.prune()
        remaining = await store.objects.references()
        assert remaining
        monkeypatch.setattr(store.objects, "remove", remove)
        if kind == ObjectKind.continuation:
            assert set(remaining) == {ref, manifest.native_state, manifest.display}
            assert await store.checkpoints.prune() == 3
        else:
            # Once the manifest is removed, a failed unlink leaves a partial
            # payload. Recovering arbitrary partials is deliberately not this GC.
            assert len(remaining) == 1 and remaining[0].object_kind == kind
            assert await store.checkpoints.prune() == 0


async def test_unreadable_manifest_is_skipped_without_blocking_other_cleanup(tmp_path):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        value = checkpoint()
        await create_owner(store, value)
        ref = await publish_continuation(store.objects, value)
        corrupt = await store.objects.publish(
            object_kind=ObjectKind.continuation, object_schema_version="1", payload={"bad": 1}
        )
        await delete_owner(store, value)
        assert await store.checkpoints.prune() == 3
        assert ref not in await store.objects.references()
        assert corrupt.ref in await store.objects.references()


async def test_pruning_ignores_in_progress_publication_without_global_access_registration(tmp_path, monkeypatch):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        deleted = checkpoint()
        await create_owner(store, deleted)
        await publish_continuation(store.objects, deleted)
        await delete_owner(store, deleted)
        value = deleted.model_copy(update={"harness_state": deleted.harness_state.fork()})
        entered, release = asyncio.Event(), asyncio.Event()
        publish = store.objects.publish_model

        async def paused(**kwargs):
            if kwargs["object_kind"] == ObjectKind.continuation:
                entered.set()
                await release.wait()
            return await publish(**kwargs)

        monkeypatch.setattr(store.objects, "publish_model", paused)
        task = asyncio.create_task(publish_continuation(store.objects, value))
        await asyncio.wait_for(entered.wait(), 5)
        try:
            assert await store.checkpoints.prune() == 3
        finally:
            release.set()
        ref = await asyncio.wait_for(task, 5)
        assert await read_continuation(store.objects, ref, include_display=True) == value


async def test_concurrent_collectors_and_bounded_passes(tmp_path, monkeypatch):
    settings = StorageSettings(data_root=tmp_path)
    async with open_local_store(settings) as first, open_local_store(settings) as second:
        value = checkpoint()
        await create_owner(first, value)
        for _ in range(4):
            await publish_continuation(first.objects, value)
        await delete_owner(first, value)
        assert await first.checkpoints.prune(limit=0) == 0
        assert await first.checkpoints.prune(limit=1) == 3
        assert len(await first.objects.references()) == 9
        inventory = first.objects.references
        both = asyncio.Event()
        entered = 0

        async def snapshot():
            nonlocal entered
            refs = await inventory()
            entered += 1
            if entered == 2:
                both.set()
            await both.wait()
            return refs

        monkeypatch.setattr(first.objects, "references", snapshot)
        monkeypatch.setattr(second.objects, "references", snapshot)
        await asyncio.wait_for(asyncio.gather(first.checkpoints.prune(), second.checkpoints.prune()), 5)
        assert not await inventory()
