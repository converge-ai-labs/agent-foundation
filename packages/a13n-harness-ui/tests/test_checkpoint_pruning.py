"""Native filesystem/OS-lock coverage shared by Linux, macOS and Windows."""

import asyncio
import sys
from contextlib import asynccontextmanager

import pytest
from a13n_harness_ui.errors import ObjectIntegrityError
from a13n_harness_ui.restart_models import RestartBatch, RestartItem
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import open_local_store
from a13n_harness_ui.storage.checkpoint_access import CheckpointAccess
from a13n_harness_ui.storage.continuations import publish_continuation, read_continuation
from a13n_harness_ui.storage.contracts import ReferencedContinuation
from a13n_harness_ui.storage.display import seal_display
from a13n_harness_ui.storage.objects import ObjectKind

from .test_continuation_objects import checkpoint
from .test_thread_repository import _configuration, _initial

pytestmark = pytest.mark.anyio


async def test_prune_deleted_checkpoints_but_retain_live_history_and_shared_prefix(tmp_path):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        value = checkpoint()
        archive = await seal_display(store.objects, value.display)
        value = value.model_copy(update={"display": archive})
        deleted = await publish_continuation(store.objects, value)
        fork = value.model_copy(update={"harness_state": value.harness_state.fork()})
        await store.threads.create(
            thread_id=fork.harness_state.thread_id, configuration=_configuration(), initial_state=_initial()
        )
        # Both historical checkpoints survive, even without a current SQL head.
        first = await publish_continuation(store.objects, fork)
        second = await publish_continuation(store.objects, fork)
        unrelated = await store.objects.publish(
            object_kind=ObjectKind.mcp_app_resource, object_schema_version="1", payload={"keep": True}
        )
        assert await store.checkpoints.prune() == 3
        refs = await store.objects.references()
        assert deleted not in refs
        assert archive.prefix in refs and unrelated.ref in refs
        for reference in (first, second):
            assert await read_continuation(store.objects, reference, include_display=True) == fork
        assert await store.checkpoints.prune() == 0


@pytest.mark.parametrize("state", ["ready", "consumed"])
async def test_restart_reference_protects_checkpoint_without_thread(tmp_path, state):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        value = checkpoint()
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
        assert await store.checkpoints.prune() == 0
        assert await read_continuation(store.objects, ref, include_display=True) == value


@pytest.mark.parametrize("kind", [ObjectKind.continuation_display, ObjectKind.continuation])
async def test_interrupted_publication_payloads_are_reclaimed(tmp_path, monkeypatch, kind):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        publish = store.objects.publish_model

        async def fail(**kwargs):
            if kwargs["object_kind"] == kind:
                raise OSError("publication unavailable")
            return await publish(**kwargs)

        monkeypatch.setattr(store.objects, "publish_model", fail)
        with pytest.raises(OSError):
            await publish_continuation(store.objects, checkpoint())
        before = await store.objects.references()
        assert before
        assert await store.checkpoints.prune() == len(before)
        assert not await store.objects.references()


@pytest.mark.parametrize(
    "kind", [ObjectKind.continuation, ObjectKind.continuation_state, ObjectKind.continuation_display]
)
async def test_unlink_failure_is_retryable_without_removing_protected_payloads(tmp_path, monkeypatch, kind):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        ref = await publish_continuation(store.objects, checkpoint())
        manifest = await store.objects.read_model(ref, ReferencedContinuation)
        remove = store.objects.remove

        async def fail(reference):
            if reference.object_kind == kind:
                raise PermissionError("Windows sharing violation")
            await remove(reference)

        monkeypatch.setattr(store.objects, "remove", fail)
        await store.checkpoints.prune()
        remaining = await store.objects.references()
        assert remaining
        if kind == ObjectKind.continuation:
            assert set(remaining) == {ref, manifest.native_state, manifest.display}
        monkeypatch.setattr(store.objects, "remove", remove)
        assert await store.checkpoints.prune() == len(remaining)
        assert not await store.objects.references()


async def test_corrupt_manifest_aborts_before_deleting_unattached_payloads(tmp_path):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        ref = await publish_continuation(store.objects, checkpoint())
        await store.objects.publish(object_kind=ObjectKind.continuation, object_schema_version="1", payload={"bad": 1})
        before = await store.objects.references()
        with pytest.raises(ObjectIntegrityError):
            await store.checkpoints.prune()
        assert await store.objects.references() == before
        assert await read_continuation(store.objects, ref)


async def test_scan_yields_to_publication_even_if_publisher_finishes_before_recheck(tmp_path, monkeypatch):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        await publish_continuation(store.objects, checkpoint())
        inventory = store.objects.references
        entered, release = asyncio.Event(), asyncio.Event()

        async def paused():
            refs = await inventory()
            entered.set()
            await release.wait()
            return refs

        monkeypatch.setattr(store.objects, "references", paused)
        pruning = asyncio.create_task(store.checkpoints.prune())
        await asyncio.wait_for(entered.wait(), 5)
        try:
            # Scanning does not hold the foreground gate. A completed reader is
            # sufficient to invalidate the scan; active-token checks alone aren't.
            async with CheckpointAccess(tmp_path).use():
                pass
        finally:
            release.set()
        assert await asyncio.wait_for(pruning, 5) == 0
        monkeypatch.setattr(store.objects, "references", inventory)
        assert await store.checkpoints.prune() == 3


async def test_active_access_and_gate_contention_skip_without_waiting(tmp_path):
    first, second = CheckpointAccess(tmp_path), CheckpointAccess(tmp_path)
    async with first.use():
        assert await asyncio.wait_for(second.snapshot(), 1) is None
    generation = await first.snapshot()
    assert generation is not None
    async with first.unchanged(generation) as idle:
        assert idle
        assert await asyncio.wait_for(second.snapshot(), 1) is None
        async with second.unchanged(generation) as other:
            assert not other
    # Errors raised inside the body must not be mistaken for acquisition failure.
    with pytest.raises(TimeoutError, match="body"):
        async with first.unchanged(generation):
            raise TimeoutError("body")


async def test_cancelled_consumer_releases_use_token(tmp_path):
    access = CheckpointAccess(tmp_path)
    entered = asyncio.Event()

    async def consumer():
        async with access.use():
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(consumer())
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await access.snapshot() is not None
    assert not list((tmp_path / "checkpoint-users").glob("*.lock"))


async def test_native_process_death_releases_use_and_allows_pruning(tmp_path):
    script = """
import asyncio, sys
from pathlib import Path
from a13n_harness_ui.storage.checkpoint_access import CheckpointAccess
async def main():
    async with CheckpointAccess(Path(sys.argv[1])).use():
        print('ready', flush=True)
        await asyncio.Event().wait()
asyncio.run(main())
"""
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        await publish_continuation(store.objects, checkpoint())
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", script, str(tmp_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            assert (await asyncio.wait_for(process.stdout.readline(), 10)).strip() == b"ready"
            assert await store.checkpoints.prune() == 0
        finally:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.communicate(), 10)
        assert await store.checkpoints.prune() == 3
        assert not list((tmp_path / "checkpoint-users").glob("*.lock"))


async def test_pruning_is_bounded_and_readers_can_enter_between_batches(tmp_path, monkeypatch):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        for _ in range(12):
            await publish_continuation(store.objects, checkpoint())
        assert await store.checkpoints.prune(limit=1) == 3
        access = store.objects.checkpoint_access
        unchanged = access.unchanged
        passes = 0

        @asynccontextmanager
        async def enter(generation):
            nonlocal passes
            passes += 1
            if passes == 2:
                async with CheckpointAccess(tmp_path).use():
                    pass
            async with unchanged(generation) as idle:
                yield idle

        monkeypatch.setattr(access, "unchanged", enter)
        assert await store.checkpoints.prune() == 30
        assert len(await store.objects.references()) == 3
