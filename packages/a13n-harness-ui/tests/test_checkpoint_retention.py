"""Run-local replacement retains final history and coordinates selected readers."""

from __future__ import annotations

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import StoreConflictError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.storage import StoredContinuation, open_local_store
from a13n_harness_ui.storage.checkpoint_lock import checkpoint_lock
from a13n_harness_ui.storage.objects import ObjectKind
from a13n_harness_ui.surfaces import RootOperationStatus
from anyio import Event, create_task_group, fail_after, sleep
from pydantic_ai.models.function import FunctionModel

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("ending", ["completed", "failed", "cancelled"])
async def test_each_run_retains_only_its_last_checkpoint_and_preserves_previous_runs(tmp_path, monkeypatch, ending):
    started, release = Event(), Event()
    calls = 0

    async def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 2:
            started.set()
            await release.wait()
            if ending == "failed":
                raise RuntimeError("Model failed after saving input")
        yield f"Answer {calls}"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=_write_configuration(tmp_path)) as app:
        thread = await app.create_thread()
        first = await app.submit_thread(thread_id=thread.thread_id, prompt="First")
        assert (await app.wait_root_operation(first.receipt_id)).status is RootOperationStatus.completed
        before = await app._store.threads.get(thread.thread_id)
        assert before is not None and before.continuation is not None
        frozen = await app._store.objects.read_model(before.continuation, StoredContinuation)
        second = await app.submit_thread(thread_id=thread.thread_id, prompt="Second")
        with fail_after(10):
            await started.wait()
        during = await app._store.threads.get(thread.thread_id)
        assert during is not None and during.continuation != before.continuation
        assert {ref for ref in await app._store.objects.references() if ref.object_kind == ObjectKind.continuation} == {
            before.continuation,
            during.continuation,
        }
        if ending == "cancelled":
            assert (await app.cancel_root_operation(second.receipt_id)).accepted
        else:
            release.set()
        assert (await app.wait_root_operation(second.receipt_id)).status.value == ending
        after = await app._store.threads.get(thread.thread_id)
        assert after is not None and after.continuation is not None
        assert {ref for ref in await app._store.objects.references() if ref.object_kind == ObjectKind.continuation} == {
            before.continuation,
            after.continuation,
        }
        assert await app._store.objects.read_model(before.continuation, StoredContinuation) == frozen
        saved = await app._store.objects.read_model(after.continuation, StoredContinuation)
        if ending != "completed":
            assert after.completion == before.completion
        async with open_local_store(settings.storage) as other:
            with pytest.raises(StoreConflictError) as stale:
                await other.read_continuation(thread.thread_id, during.continuation)
            assert stale.value.code == "thread_continuation_conflict"
            assert await other.read_continuation(thread.thread_id, after.continuation) == saved


async def test_selected_reader_in_another_store_waits_for_replacement_lock(tmp_path, monkeypatch):
    async def model(messages, info):
        yield "Saved"

    async def resolve(self, context, model_id):
        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=_write_configuration(tmp_path)) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="First")
        await app.wait_root_operation(receipt.receipt_id)
        head = await app._store.threads.get(thread.thread_id)
        assert head is not None and head.continuation is not None
        started, done = Event(), Event()
        async with open_local_store(settings.storage) as other:

            async def read():
                started.set()
                await other.read_continuation(thread.thread_id, head.continuation)
                done.set()

            async with create_task_group() as group:
                async with checkpoint_lock(settings.storage.data_root, thread.thread_id):
                    group.start_soon(read)
                    await started.wait()
                    await sleep(0.1)
                    assert not done.is_set()
                with fail_after(5):
                    await done.wait()
