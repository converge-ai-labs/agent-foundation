"""Storage maintenance is automatic and cannot tear down the App on failure."""

import asyncio

import pytest
from a13n_harness_ui.app import AppState, open_harness_ui_app
from a13n_harness_ui.compaction import BackgroundCompaction
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.storage import open_local_store
from a13n_harness_ui.storage.checkpoint_pruning import CheckpointPruner
from a13n_harness_ui.storage.continuations import publish_continuation
from a13n_harness_ui.surfaces import RootOperationStatus
from a13n_harness_ui.thread_files import ThreadFiles
from pydantic_ai.models.function import FunctionModel

from .test_app import _settings, _write_configuration
from .test_checkpoint_pruning import create_owner, delete_owner
from .test_continuation_objects import checkpoint

pytestmark = pytest.mark.anyio


async def _resolve(self, context, model_id):
    async def model(messages, info):
        yield "Saved response"

    return FunctionModel(stream_function=model)


@pytest.mark.parametrize("failed", ["scratch", "compaction", "checkpoint"])
async def test_failure_is_isolated_and_next_pass_and_app_lifecycle_work(tmp_path, monkeypatch, failed, caplog):
    monkeypatch.setattr(HarnessUiModelResolver, "__call__", _resolve)
    seen, finished = [], asyncio.Event()
    fail = True

    def pruner(kind):
        async def prune(self, *args, **kwargs):
            seen.append(kind)
            if kind == "checkpoint":
                finished.set()
            if kind == failed and fail:
                raise OSError("private storage path must not be logged")

        return prune

    monkeypatch.setattr(ThreadFiles, "prune", pruner("scratch"))
    monkeypatch.setattr(BackgroundCompaction, "prune", pruner("compaction"))
    monkeypatch.setattr(CheckpointPruner, "prune", pruner("checkpoint"))
    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path)
    ) as app:
        await asyncio.wait_for(finished.wait(), 5)
        assert seen == ["scratch", "compaction", "checkpoint"]
        assert app.state is AppState.ready
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="still works")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        fail = False
        await app._prune_storage()
        assert seen == ["scratch", "compaction", "checkpoint"] * 2
    assert app.state is AppState.closed
    assert "Storage pruning failed" in caplog.text
    assert "private storage path" not in caplog.text


async def test_checkpoint_cleanup_runs_automatically_on_startup(tmp_path, monkeypatch):
    settings = _settings(tmp_path / "state")
    async with open_local_store(settings.storage) as store:
        value = checkpoint()
        await create_owner(store, value)
        orphan = await publish_continuation(store.objects, value)
        await delete_owner(store, value)
    done = asyncio.Event()
    prune = CheckpointPruner.prune

    async def observe(self, **kwargs):
        result = await prune(self, **kwargs)
        done.set()
        return result

    monkeypatch.setattr(CheckpointPruner, "prune", observe)
    async with open_harness_ui_app(settings, configuration_path=_write_configuration(tmp_path)) as app:
        await asyncio.wait_for(done.wait(), 5)
        assert orphan not in await app._store.objects.references()
        assert await app.create_thread()


async def test_slow_maintenance_does_not_block_startup_or_shutdown(tmp_path, monkeypatch):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def paused(self, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(CheckpointPruner, "prune", paused)
    async with asyncio.timeout(5):
        async with open_harness_ui_app(
            _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path)
        ) as app:
            await entered.wait()
            assert app.state is AppState.ready
            assert await app.create_thread()
    assert cancelled.is_set()
    assert app.state is AppState.closed


@pytest.mark.parametrize("operation", ["fork", "clear", "run"])
async def test_cleanup_of_deleted_thread_does_not_touch_in_progress_sql_selection(tmp_path, monkeypatch, operation):
    monkeypatch.setattr(HarnessUiModelResolver, "__call__", _resolve)
    async with open_harness_ui_app(
        _settings(tmp_path / "state"), configuration_path=_write_configuration(tmp_path)
    ) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="first")
        await app.wait_root_operation(receipt.receipt_id)
        selected = await app._store.threads.get(thread.thread_id)
        before_garbage = set(await app._store.objects.references())
        deleted = checkpoint()
        await create_owner(app._store, deleted)
        await publish_continuation(app._store.objects, deleted)
        await delete_owner(app._store, deleted)
        garbage = set(await app._store.objects.references()) - before_garbage
        entered, release = asyncio.Event(), asyncio.Event()
        repository = app._store.threads
        name = "fork" if operation == "fork" else "select_continuation"
        original = getattr(repository, name)

        async def paused(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(repository, name, paused)

        async def perform():
            if operation == "fork":
                return await app.fork_thread(thread.thread_id)
            if operation == "clear":
                return await app._threads.clear_context(
                    thread_id=thread.thread_id, expected_continuation_id=selected.continuation.logical_digest
                )
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="second")
            return await app.wait_root_operation(receipt.receipt_id)

        pending = asyncio.create_task(perform())
        await asyncio.wait_for(entered.wait(), 10)
        try:
            before = set(await app._store.objects.references())
            assert await app._store.checkpoints.prune() == 3
            assert set(await app._store.objects.references()) == before - garbage
        finally:
            release.set()
        await asyncio.wait_for(pending, 10)
        assert await app._store.checkpoints.prune() == 0
