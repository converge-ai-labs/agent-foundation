"""Run-local replacement retains final history and coordinates selected readers."""

from __future__ import annotations

import asyncio
import gc
import weakref
from contextvars import ContextVar
from importlib import import_module

import pytest
from a13n_harness import HarnessState
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import StoreConflictError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.storage import open_local_store
from a13n_harness_ui.storage.checkpoint_lock import checkpoint_lock
from a13n_harness_ui.storage.continuations import read_continuation
from a13n_harness_ui.storage.objects import ObjectKind
from a13n_harness_ui.surfaces import RootOperationStatus
from anyio import CancelScope, Event, create_task_group, fail_after, sleep, to_thread
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
        frozen = await read_continuation(app._store.objects, before.continuation, include_display=True)
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
        assert await read_continuation(app._store.objects, before.continuation, include_display=True) == frozen
        saved = await read_continuation(app._store.objects, after.continuation, include_display=True)
        refs = await app._store.objects.references()
        assert sum(ref.object_kind is ObjectKind.continuation_state for ref in refs) == 2
        assert sum(ref.object_kind is ObjectKind.continuation_display for ref in refs) == 2
        if ending != "completed":
            assert after.completion == before.completion
        async with open_local_store(settings.storage) as other:
            with pytest.raises(StoreConflictError) as stale:
                await other.read_continuation(thread.thread_id, during.continuation)
            assert stale.value.code == "thread_continuation_conflict"
            assert await other.read_continuation(thread.thread_id, after.continuation, include_display=True) == saved


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


async def test_uncontended_checkpoint_lock_does_not_retain_callers_context(tmp_path):
    current: ContextVar[HarnessState] = ContextVar("checkpoint-caller")
    state = HarnessState.new()
    reference = weakref.ref(state)
    # Keep a live earlier timer ahead of any cancelled acquisition deadline.
    earlier = asyncio.get_running_loop().call_later(10, lambda: None)
    token = current.set(state)
    try:
        async with checkpoint_lock(tmp_path, "thread-context"):
            assert current.get() is state
    finally:
        current.reset(token)
        del state
    try:
        # Reuse the worker without the caller context before checking ownership.
        await to_thread.run_sync(lambda: None)
        gc.collect()
        assert reference() is None
    finally:
        earlier.cancel()


async def test_checkpoint_lock_contention_keeps_its_deadline(tmp_path, monkeypatch):
    module = import_module("a13n_harness_ui.storage.checkpoint_lock")
    async with checkpoint_lock(tmp_path, "thread-timeout"):
        clock = 100.0
        waits = []

        async def advance(delay):
            nonlocal clock
            waits.append(delay)
            clock += 10

        monkeypatch.setattr(module, "current_time", lambda: clock)
        monkeypatch.setattr(module, "sleep", advance)
        with pytest.raises(TimeoutError):
            async with checkpoint_lock(tmp_path, "thread-timeout"):
                pytest.fail("A competing lock must not enter")
        assert len(waits) == 3
        assert all(0 < delay <= 0.05 for delay in waits)
    # Timeout neither releases someone else's lock nor poisons later acquisition.
    async with checkpoint_lock(tmp_path, "thread-timeout"):
        pass


async def test_checkpoint_lock_cancellation_releases_acquired_lock(tmp_path):
    with CancelScope() as scope:
        async with checkpoint_lock(tmp_path, "thread-cancel"):
            scope.cancel()
            await sleep(0)
    with fail_after(2):
        async with checkpoint_lock(tmp_path, "thread-cancel"):
            pass


async def test_checkpoint_lock_cancellation_while_waiting_preserves_holder(tmp_path, monkeypatch):
    module = import_module("a13n_harness_ui.storage.checkpoint_lock")
    waiting = Event()
    original_sleep = module.sleep

    async def observed_sleep(delay):
        waiting.set()
        await original_sleep(delay)

    monkeypatch.setattr(module, "sleep", observed_sleep)
    async with checkpoint_lock(tmp_path, "thread-wait"):
        async with create_task_group() as group:

            async def contender():
                async with checkpoint_lock(tmp_path, "thread-wait"):
                    pytest.fail("The holder still owns this lock")

            group.start_soon(contender)
            await waiting.wait()
            group.cancel_scope.cancel()
        with pytest.raises(TimeoutError), fail_after(0.1):
            async with checkpoint_lock(tmp_path, "thread-wait"):
                pytest.fail("Cancelling a waiter must not release the holder")
    with fail_after(2):
        async with checkpoint_lock(tmp_path, "thread-wait"):
            pass
