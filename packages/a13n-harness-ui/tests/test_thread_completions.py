"""Durable successful results, independent of receipts and browser observation."""

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition import AgentReconstructor
from a13n_harness_ui.errors import StoreConflictError
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.storage import ObjectKind, ObjectRef
from a13n_harness_ui.surfaces import RootOperationStatus, ThreadMetadataMutation, ThreadMetadataPatch
from anyio import Event, fail_after, sleep_forever
from pydantic_ai.exceptions import UnexpectedModelBehavior

from .test_app import _reconstructed, _write_configuration

pytestmark = pytest.mark.anyio


async def test_success_survives_later_failure_cancellation_and_app_restart(tmp_path, monkeypatch):
    configuration = _write_configuration(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    mode = "success"
    started = Event()

    async def stream(messages, info):
        if mode == "fail":
            yield "Partial output"
            raise UnexpectedModelBehavior("Expected test failure")
        if mode == "wait":
            started.set()
            await sleep_forever()
        yield "A saved result."

    def reconstruct(self, composition, *, root_capabilities=(), **kwargs):
        return _reconstructed(stream, root_capabilities)

    monkeypatch.setattr(AgentReconstructor, "reconstruct", reconstruct)
    async with open_harness_ui_app(settings, configuration_path=configuration) as app:
        thread = await app.create_thread()
        assert thread.completion is None
        for version in (1, 2):
            receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Complete")
            outcome = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
            assert outcome.status is RootOperationStatus.completed
            detail = await app.get_thread(thread.thread_id)
            marker = detail.thread.completion
            assert marker is not None and marker.version == version
            assert marker.run_id == outcome.run_id
            assert marker.continuation_id == detail.continuation_id
            saved = await app.get_thread_transcript(thread_id=thread.thread_id)
            assert saved.completion_version == version
        mode = "fail"
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Fail")
        assert (
            await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        ).status is RootOperationStatus.failed
        assert (await app.get_thread(thread.thread_id)).thread.completion == marker
        mode = "wait"
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Wait")
        with fail_after(10):
            await started.wait()
        # A request checkpoint selects a different history but retains the last success.
        during = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert during.completion_version == 2
        assert during.continuation_id != marker.continuation_id
        await app.cancel_root_operation(receipt.receipt_id)
        await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        detail = await app.get_thread(thread.thread_id)
        assert detail.thread.completion == marker
        await app.update_thread_metadata(
            thread_id=thread.thread_id,
            mutation=ThreadMetadataMutation(
                expected_version=detail.thread.metadata_version, patch=ThreadMetadataPatch(archived=True)
            ),
        )
    async with open_harness_ui_app(settings, configuration_path=configuration) as app:
        page = await app.lookup_threads(thread_ids=(thread.thread_id, thread.thread_id, "missing"))
        assert page.total == 1 and page.next_cursor is None
        assert page.threads[0].completion == marker
        assert page.threads[0].archived
        saved = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert saved.completion_version == 2


async def test_selection_conflict_does_not_publish_completion_and_history_uses_its_snapshot(tmp_path, monkeypatch):
    configuration = _write_configuration(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=configuration) as app:
        thread = await app.create_thread()
        ref = ObjectRef(object_kind=ObjectKind.continuation, object_schema_version="1", logical_digest="a" * 64)
        original = app._projections._history

        async def history(snapshot):
            result = await original(snapshot)
            # A new completion is published after history chose the initial snapshot.
            await app._store.threads.select_continuation(
                thread_id=thread.thread_id, expected=None, replacement=ref, completed_run_id="run-test"
            )
            return result

        monkeypatch.setattr(app._projections, "_history", history)
        saved = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert saved.completion_version == 0
        assert saved.continuation_id.startswith("initial:")
        stored = await app._store.threads.get(thread.thread_id)
        assert stored.completion.version == 1
        with pytest.raises(StoreConflictError):
            await app._store.threads.select_continuation(
                thread_id=thread.thread_id, expected=None, replacement=ref, completed_run_id="run-other"
            )
        # An idempotent repeat of the same successful Run does not produce a second result.
        repeated = await app._store.threads.select_continuation(
            thread_id=thread.thread_id, expected=ref, replacement=ref, completed_run_id="run-test"
        )
        assert repeated.completion == stored.completion
