from __future__ import annotations

import json
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.display_history import saved_display_history
from a13n_harness_ui.errors import StoreConflictError, ThreadError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.storage import StoredThreadInitialState
from a13n_harness_ui.surfaces import NewThreadDefaults, RootOperationStatus, ThreadMetadataMutation, ThreadMetadataPatch
from a13n_harness_ui.thread_files import AttachmentUpload
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


async def test_fork_preserves_saved_conversation_work_and_files_and_continues_independently(
    tmp_path: Path, monkeypatch
):
    path = _write_configuration(tmp_path)
    calls: list[str] = []

    async def resolve(self, context, model_id):
        async def model(messages, info):
            calls.append(ModelMessagesTypeAdapter.dump_json(messages).decode())
            if len(calls) == 1:
                yield {
                    0: DeltaToolCall(
                        name="note_write", json_args='{"key":"decision","value":"Keep this note"}', tool_call_id="note"
                    ),
                    1: DeltaToolCall(
                        name="task_create",
                        json_args='{"subject":"Keep this task","description":"Task details"}',
                        tool_call_id="task",
                    ),
                }
            else:
                yield "Original answer" if len(calls) == 2 else "Fork answer"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(settings, configuration_path=path, instrumentation=None) as app:
        source = await app.create_thread(title="Same title")
        receipt = await app.submit_thread(thread_id=source.thread_id, prompt="Original question")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        source = await app._threads.get(source.thread_id)
        assert source.continuation is not None
        original = await app._store.read_continuation(source.thread_id, source.continuation, include_display=True)
        attachment = await app._thread_files.stage(source.thread_id, AttachmentUpload("notes.txt", b"saved attachment"))
        await app._thread_files.retain(source.thread_id, attachment.attachment_id)
        source_dir = app._thread_files.directory(source.thread_id)
        (source_dir / "tmp" / "script.py").write_text("original scratch")
        copied = await app.fork_thread(source.thread_id)
        assert len(calls) == 2
        assert copied.thread_id != source.thread_id
        assert copied.title == source.title
        assert copied.role == "ordinary" and copied.root_activity.state == "inactive"
        assert copied.last_execution is None and copied.completion is None
        saved = await app._threads.get(copied.thread_id)
        assert saved.configuration == source.configuration.model_copy(update={"version": 1})
        assert saved.continuation is not None and saved.continuation != source.continuation
        forked = await app._store.read_continuation(copied.thread_id, saved.continuation, include_display=True)
        assert forked.harness_state.thread_id == copied.thread_id
        assert forked.harness_state.message_history == original.harness_state.message_history
        assert forked.harness_state.agent_context_state == original.harness_state.agent_context_state
        assert saved_display_history(forked.harness_state) is None
        assert forked.display == original.display and forked.display is not None
        assert forked.run_composition == original.run_composition
        assert (await app.thread_notes(thread_id=copied.thread_id)).total == 1
        assert (await app.thread_tasks(thread_id=copied.thread_id)).total == 1
        assert (await app._thread_files.read(copied.thread_id, attachment.attachment_id))[1] == b"saved attachment"
        copied_dir = app._thread_files.directory(copied.thread_id)
        assert (copied_dir / "tmp" / "script.py").read_text() == "original scratch"
        (copied_dir / "tmp" / "script.py").write_text("fork scratch")
        assert (source_dir / "tmp" / "script.py").read_text() == "original scratch"
        history = json.dumps((await app.get_thread_transcript(thread_id=copied.thread_id)).model_dump(mode="json"))
        assert "Original question" in history and "Original answer" in history

    async with open_harness_ui_app(settings, configuration_path=path, instrumentation=None) as app:
        receipt = await app.submit_thread(thread_id=copied.thread_id, prompt="Continue only the fork")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        assert "Original question" in calls[-1] and "Original answer" in calls[-1]
        assert "Keep this note" in calls[-1] and "Keep this task" in calls[-1]
        assert (await app._threads.get(source.thread_id)).continuation == source.continuation
        fork_history = json.dumps((await app.get_thread_transcript(thread_id=copied.thread_id)).model_dump(mode="json"))
        source_history = json.dumps(
            (await app.get_thread_transcript(thread_id=source.thread_id)).model_dump(mode="json")
        )
        assert "Fork answer" in fork_history and "Fork answer" not in source_history


async def test_fork_empty_projectless_thread_retains_metadata(tmp_path: Path):
    path = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path) as app:
        source = await app.create_thread(defaults=NewThreadDefaults(project_id=None), title="Untouched")
        await app.update_thread_metadata(
            thread_id=source.thread_id,
            mutation=ThreadMetadataMutation(
                expected_version=source.metadata_version, patch=ThreadMetadataPatch(archived=True, starred=True)
            ),
        )
        copied = await app.fork_thread(source.thread_id)
        assert copied.title == "Untouched" and copied.archived and copied.starred
        assert copied.configuration.project_id is None
        stored = await app._threads.get(copied.thread_id)
        assert stored.continuation is None
        initial = await app._store.objects.read_model(stored.initial_state, StoredThreadInitialState)
        assert initial.harness_state.thread_id == copied.thread_id
        assert not initial.harness_state.message_history


async def test_fork_rejects_coordinators_workers_children_and_internal_threads(tmp_path: Path):
    path = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path) as app:
        coordinator = await app.create_thread(coordinator=True)
        worker = await app.create_thread(coordinator_thread_id=coordinator.thread_id)
        source = await app._threads.get(worker.thread_id)
        child = await app._store.threads.create(
            thread_id="thread_child",
            parent_thread_id=worker.thread_id,
            configuration=source.configuration,
            initial_state=source.initial_state,
        )
        memory = await app._threads.memory_thread(scope="global", project_id=None, model_id="model-main")
        live = await app._threads.live_thread(configuration_root=path.parent)
        for thread in (coordinator, worker, child, memory, live):
            with pytest.raises(ThreadError) as failure:
                await app.fork_thread(thread.thread_id)
            assert failure.value.code == "thread_fork_unsupported"
        assert (await app.list_threads()).total == 3  # Coordinator, worker and Live; children/memory excluded.


@pytest.mark.parametrize("change", ["metadata", "coordinator"])
async def test_fork_rechecks_source_and_removes_files_on_conflict(tmp_path: Path, monkeypatch, change: str):
    path = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path) as app:
        source = await app.create_thread(title="Source")
        publish = app._store.threads.fork

        async def conflict(**kwargs):
            if change == "coordinator":
                await app.promote_coordinator(source.thread_id)
            else:
                await app.update_thread_metadata(
                    thread_id=source.thread_id,
                    mutation=ThreadMetadataMutation(expected_version=1, patch=ThreadMetadataPatch(title="Changed")),
                )
            return await publish(**kwargs)

        monkeypatch.setattr(app._store.threads, "fork", conflict)
        with pytest.raises(StoreConflictError):
            await app.fork_thread(source.thread_id)
        assert (await app.list_threads()).total == 1
        assert [p.name for p in app._thread_files.root.iterdir()] == [source.thread_id]


async def test_fork_copy_failure_publishes_no_thread(tmp_path: Path, monkeypatch):
    path = _write_configuration(tmp_path)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path) as app:
        source = await app.create_thread()

        def fail_copy(*args, **kwargs):
            raise OSError("copy failed")

        monkeypatch.setattr("a13n_harness_ui.thread_files.shutil.copytree", fail_copy)
        with pytest.raises(OSError, match="copy failed"):
            await app.fork_thread(source.thread_id)
        assert (await app.list_threads()).total == 1
        assert [p.name for p in app._thread_files.root.iterdir()] == [source.thread_id]


async def test_fork_retains_pending_decision_without_copying_its_live_timeout(tmp_path: Path, monkeypatch):
    path = _write_configuration(tmp_path)

    async def resolve(self, context, model_id):
        async def model(messages, info):
            yield {
                0: DeltaToolCall(
                    name="ask_user_question",
                    json_args=json.dumps(
                        {
                            "questions": [
                                {
                                    "header": "Choice",
                                    "question": "Choose a path",
                                    "options": [
                                        {"label": "First", "description": "First path"},
                                        {"label": "Second", "description": "Second path"},
                                    ],
                                }
                            ]
                        }
                    ),
                    tool_call_id="question",
                )
            }

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(
        _settings(tmp_path / "data"), configuration_path=path, host_mode="webui", instrumentation=None
    ) as app:
        source = await app.create_thread()
        receipt = await app.submit_thread(thread_id=source.thread_id, prompt="Ask me")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.suspended
        before = await app.get_thread(source.thread_id)
        copied = await app.fork_thread(source.thread_id)
        after = await app.get_thread(copied.thread_id)
        assert after.deferred_requests == before.deferred_requests
        assert "respond" in after.available_actions
        assert source.thread_id in app._root_runs._interaction_waits
        assert copied.thread_id not in app._root_runs._interaction_waits


async def test_fork_active_thread_copies_only_saved_state(tmp_path: Path, monkeypatch):
    from anyio import Event, fail_after

    path = _write_configuration(tmp_path)
    streaming = Event()
    release = Event()

    async def resolve(self, context, model_id):
        async def model(messages, info):
            yield "Unsaved live output"
            streaming.set()
            await release.wait()

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path, instrumentation=None) as app:
        source = await app.create_thread(title="Active source")
        receipt = await app.submit_thread(thread_id=source.thread_id, prompt="Saved input")
        try:
            with fail_after(5):
                await streaming.wait()
            copied = await app.fork_thread(source.thread_id)
            history = json.dumps((await app.get_thread_transcript(thread_id=copied.thread_id)).model_dump(mode="json"))
            assert "Saved input" in history and "Unsaved live output" not in history
            assert copied.root_activity.state == "inactive"
            assert (await app.get_thread(source.thread_id)).thread.root_activity.state == "running"
        finally:
            release.set()
            assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
