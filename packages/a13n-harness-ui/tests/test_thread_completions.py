"""Durable successful results, independent of receipts and browser observation."""

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.composition import AgentReconstructor
from a13n_harness_ui.errors import StoreConflictError
from a13n_harness_ui.settings import HarnessUiSettings, StorageSettings
from a13n_harness_ui.storage import ObjectKind, ObjectRef
from a13n_harness_ui.storage.contracts import ThreadReadModel
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
        original = app._projections._inspection_header

        async def inspection_header(snapshot):
            result = await original(snapshot)
            # A new completion is published after history chose the initial snapshot.
            await app._store.threads.select_continuation(
                thread_id=thread.thread_id,
                expected=None,
                replacement=ref,
                read_model=ThreadReadModel(),
                completed_run_id="run-test",
            )
            return result

        monkeypatch.setattr(app._projections, "_inspection_header", inspection_header)
        saved = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert saved.completion_version == 0
        assert saved.continuation_id.startswith("initial:")
        stored = await app._store.threads.get(thread.thread_id)
        assert stored.completion.version == 1
        with pytest.raises(StoreConflictError):
            await app._store.threads.select_continuation(
                thread_id=thread.thread_id,
                expected=None,
                replacement=ref,
                read_model=ThreadReadModel(),
                completed_run_id="run-other",
            )
        # An idempotent repeat of the same successful Run does not produce a second result.
        repeated = await app._store.threads.select_continuation(
            thread_id=thread.thread_id,
            expected=ref,
            replacement=ref,
            read_model=ThreadReadModel(),
            completed_run_id="run-test",
        )
        assert repeated.completion == stored.completion


@pytest.mark.parametrize("preparation", [False, True])
async def test_latest_failure_survives_restart_without_receipts(tmp_path, monkeypatch, preparation):
    from a13n_harness_ui.errors import RunCoordinationError, ThreadError

    configuration = _write_configuration(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)

    async def stream(messages, info):
        yield "Partial output"
        raise UnexpectedModelBehavior("Expected test failure")

    def reconstruct(self, composition, *, root_capabilities=(), **kwargs):
        if preparation:
            raise ThreadError("Preparation failed", code="test_preparation_failed")
        return _reconstructed(stream, root_capabilities)

    monkeypatch.setattr(AgentReconstructor, "reconstruct", reconstruct)
    async with open_harness_ui_app(settings, configuration_path=configuration) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Fail")
        operation = await app.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        assert operation.status is RootOperationStatus.failed
        detail = await app.get_thread(thread.thread_id)
        saved = detail.thread.last_execution
        assert saved.status == "failed"
        assert saved.execution_id == receipt.receipt_id
        assert saved.completed_at is not None
        assert saved.error_message
        assert saved.run_id == operation.run_id
        if preparation:
            assert saved.run_id is None
            assert detail.continuation_id is None
        # Rejected input does not overwrite the last admitted execution.
        with pytest.raises(RunCoordinationError, match="must not be blank"):
            await app.submit_thread(thread_id=thread.thread_id, prompt="  ")
        assert (await app.get_thread(thread.thread_id)).thread.last_execution == saved
    async with open_harness_ui_app(settings, configuration_path=configuration) as app:
        detail = await app.get_thread(thread.thread_id)
        assert detail.thread.last_execution == saved
        assert detail.thread.root_activity.state == "inactive"
        assert await app.active_root_operation(thread.thread_id) is None
        assert (await app.lookup_threads(thread_ids=(thread.thread_id,))).threads[0].last_execution == saved


async def test_foreign_active_execution_is_unknown_without_rewriting_shared_state(tmp_path, monkeypatch):
    configuration = _write_configuration(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    started = Event()

    async def stream(messages, info):
        started.set()
        await sleep_forever()
        yield "unreachable"

    monkeypatch.setattr(
        AgentReconstructor,
        "reconstruct",
        lambda self, composition, root_capabilities=(), **kwargs: _reconstructed(stream, root_capabilities),
    )
    async with open_harness_ui_app(settings, configuration_path=configuration) as owner:
        thread = await owner.create_thread()
        receipt = await owner.submit_thread(thread_id=thread.thread_id, prompt="Wait")
        with fail_after(10):
            await started.wait()
        assert (await owner.get_thread(thread.thread_id)).thread.last_execution.status == "running"
        async with open_harness_ui_app(settings, configuration_path=configuration) as observer:
            detail = await observer.get_thread(thread.thread_id)
            assert detail.thread.last_execution.status == "unknown"
            assert detail.thread.last_execution.completed_at is None
            assert detail.thread.root_activity.available_actions == ()
            assert (await observer._store.threads.get(thread.thread_id)).last_execution.status == "running"
        await owner.cancel_root_operation(receipt.receipt_id)
        await owner.wait_root_operation(receipt.receipt_id, timeout_seconds=10)
        assert (await owner.get_thread(thread.thread_id)).thread.last_execution.status == "cancelled"
    async with open_harness_ui_app(settings, configuration_path=configuration) as app:
        assert (await app.get_thread(thread.thread_id)).thread.last_execution.status == "cancelled"


async def test_abandoned_attempt_is_unknown_and_late_terminal_cannot_replace_new_attempt(tmp_path):
    from datetime import UTC, datetime

    from a13n_harness_ui.storage.contracts import ThreadExecution

    configuration = _write_configuration(tmp_path)
    settings = HarnessUiSettings(storage=StorageSettings(data_root=tmp_path / "data"), pricing_auto_update=False)
    async with open_harness_ui_app(settings, configuration_path=configuration) as app:
        thread = await app.create_thread()
        old = ThreadExecution(execution_id="receipt-old", status="preparing", submitted_at=datetime.now(UTC))
        assert await app._store.threads.save_execution(thread.thread_id, old)
    async with open_harness_ui_app(settings, configuration_path=configuration) as app:
        assert (await app.get_thread(thread.thread_id)).thread.last_execution.status == "unknown"
        new = old.model_copy(update={"execution_id": "receipt-new"})
        assert await app._store.threads.save_execution(thread.thread_id, new)
        late = old.model_copy(update={"status": "failed", "completed_at": datetime.now(UTC)})
        assert not await app._store.threads.save_execution(thread.thread_id, late)
        assert (await app._store.threads.get(thread.thread_id)).last_execution == new
