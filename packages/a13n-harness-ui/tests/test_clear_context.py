from __future__ import annotations

import json
from pathlib import Path

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.display_history import saved_display_history
from a13n_harness_ui.errors import RunCoordinationError, StoreConflictError, ThreadError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.storage import StoredContinuation
from a13n_harness_ui.surfaces import RootOperationStatus, ThreadMetadataMutation, ThreadMetadataPatch
from anyio import Event, fail_after
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


def transcript_content(entries):
    # Saved-output references are rebound to the new continuation; content and
    # stable message positions must remain unchanged.
    return [entry.model_dump(exclude={"parts": {"__all__": {"comment_target"}}}) for entry in entries]


async def test_clear_context_retains_history_but_restarts_model_and_working_state(tmp_path: Path, monkeypatch) -> None:
    path = _write_configuration(tmp_path)
    calls: list[str] = []

    async def resolve(self, context, model_id):
        async def model(messages, info):
            calls.append(ModelMessagesTypeAdapter.dump_json(messages).decode())
            if len(calls) == 1:
                yield {
                    0: DeltaToolCall(
                        name="note_write",
                        json_args='{"key":"decision","value":"Old remembered decision"}',
                        tool_call_id="note",
                    ),
                    1: DeltaToolCall(
                        name="task_create",
                        json_args='{"subject":"Old task","description":"Old task details"}',
                        tool_call_id="task",
                    ),
                }
            else:
                yield "Old answer" if len(calls) == 2 else "Fresh answer"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    settings = _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})
    async with open_harness_ui_app(settings, configuration_path=path, instrumentation=None) as app:
        thread = await app.create_thread(title="Keep this conversation")
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Old prompt")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        assert (await app.thread_notes(thread_id=thread.thread_id)).total == 1
        assert (await app.thread_tasks(thread_id=thread.thread_id)).total == 1
        before = await app.get_thread(thread.thread_id)
        history = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert before.continuation_id is not None
        assert "clear_context" in before.available_actions
        root = app._thread_files.directory(thread.thread_id)
        # Context clearing must not remove retained Thread files.
        root.mkdir(parents=True, exist_ok=True)
        retained_file = root / "keep.txt"
        retained_file.write_text("retained")
        cleared = await app.clear_thread_context(
            thread_id=thread.thread_id, expected_continuation_id=before.continuation_id
        )
        assert len(calls) == 2
        assert cleared.continuation_id != before.continuation_id
        assert cleared.thread.configuration == before.thread.configuration
        assert cleared.thread.title == before.thread.title
        assert cleared.thread.completion == before.thread.completion
        assert retained_file.read_text() == "retained"
        assert transcript_content(
            (await app.get_thread_transcript(thread_id=thread.thread_id)).entries
        ) == transcript_content(history.entries)
        assert (await app.thread_notes(thread_id=thread.thread_id)).total == 0
        assert (await app.thread_tasks(thread_id=thread.thread_id)).total == 0
        assert (await app.context_usage(thread.thread_id)).latest_request_tokens is None
        selected = await app._store.threads.get(thread.thread_id)
        assert selected is not None and selected.continuation is not None
        stored = await app._store.objects.read_model(selected.continuation, StoredContinuation)
        assert not stored.harness_state.message_history
        assert set(stored.harness_state.agent_context_state.entries) == {"a13n.harness-ui.display-history"}
        assert saved_display_history(stored.harness_state) is not None
        with pytest.raises(ThreadError, match="context changed"):
            await app.clear_thread_context(thread_id=thread.thread_id, expected_continuation_id=before.continuation_id)

    async with open_harness_ui_app(settings, configuration_path=path, instrumentation=None) as app:
        assert transcript_content(
            (await app.get_thread_transcript(thread_id=thread.thread_id)).entries
        ) == transcript_content(history.entries)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Fresh prompt")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.completed
        assert len(calls) == 3
        assert "Fresh prompt" in calls[-1]
        for old in ("Old prompt", "Old answer", "Old remembered decision", "Old task"):
            assert old not in calls[-1]
        after = await app.get_thread_transcript(thread_id=thread.thread_id)
        assert transcript_content(after.entries[: len(history.entries)]) == transcript_content(history.entries)
        text = json.dumps(after.model_dump(mode="json"))
        assert "Old prompt" in text and "Fresh prompt" in text and "Fresh answer" in text


async def test_clear_context_rejects_active_initial_and_archived_threads(tmp_path: Path, monkeypatch) -> None:
    path = _write_configuration(tmp_path)
    started, release = Event(), Event()

    async def resolve(self, context, model_id):
        async def model(messages, info):
            started.set()
            await release.wait()
            yield "done"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path, instrumentation=None) as app:
        thread = await app.create_thread()
        assert "clear_context" not in (await app.get_thread(thread.thread_id)).available_actions
        with pytest.raises(ThreadError):
            await app.clear_thread_context(thread_id=thread.thread_id, expected_continuation_id="a" * 64)
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Busy")
        try:
            with fail_after(10):
                await started.wait()
            detail = await app.get_thread(thread.thread_id)
            assert detail.continuation_id is not None
            assert "clear_context" not in detail.available_actions
            with pytest.raises(RunCoordinationError) as error:
                await app.clear_thread_context(
                    thread_id=thread.thread_id, expected_continuation_id=detail.continuation_id
                )
            assert error.value.code == "thread_run_active"
        finally:
            release.set()
        await app.wait_root_operation(receipt.receipt_id)
        detail = await app.get_thread(thread.thread_id)
        await app.update_thread_metadata(
            thread_id=thread.thread_id,
            mutation=ThreadMetadataMutation(
                expected_version=thread.metadata_version, patch=ThreadMetadataPatch(archived=True)
            ),
        )
        with pytest.raises(ThreadError) as error:
            await app.clear_thread_context(thread_id=thread.thread_id, expected_continuation_id=detail.continuation_id)
        assert error.value.code == "thread_archived"


@pytest.mark.parametrize("failure", ["publication", "selection"])
async def test_clear_context_failure_preserves_selected_head(tmp_path: Path, monkeypatch, failure: str) -> None:
    path = _write_configuration(tmp_path)

    async def resolve(self, context, model_id):
        async def model(messages, info):
            yield "Saved response"

        return FunctionModel(stream_function=model)

    monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)
    async with open_harness_ui_app(_settings(tmp_path / "data"), configuration_path=path, instrumentation=None) as app:
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Keep me")
        await app.wait_root_operation(receipt.receipt_id)
        before = await app.get_thread(thread.thread_id)

        async def fail(**kwargs):
            if failure == "publication":
                raise OSError("Cannot publish")
            raise StoreConflictError("Concurrent writer", code="thread_continuation_conflict")

        if failure == "publication":
            monkeypatch.setattr(app._store.objects, "publish_model", fail)
        else:
            monkeypatch.setattr(app._store.threads, "select_continuation", fail)
        with pytest.raises((OSError, StoreConflictError)):
            await app.clear_thread_context(thread_id=thread.thread_id, expected_continuation_id=before.continuation_id)
        assert (await app.get_thread(thread.thread_id)).continuation_id == before.continuation_id


async def test_clear_context_discards_deferred_requests_and_old_timeout(tmp_path: Path, monkeypatch) -> None:
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
        thread = await app.create_thread()
        receipt = await app.submit_thread(thread_id=thread.thread_id, prompt="Ask me")
        assert (await app.wait_root_operation(receipt.receipt_id)).status is RootOperationStatus.suspended
        before = await app.get_thread(thread.thread_id)
        assert before.deferred_requests
        assert thread.thread_id in app._root_runs._interaction_waits
        cleared = await app.clear_thread_context(
            thread_id=thread.thread_id, expected_continuation_id=before.continuation_id
        )
        assert not cleared.deferred_requests
        assert "run" in cleared.available_actions and "respond" not in cleared.available_actions
        assert thread.thread_id not in app._root_runs._interaction_waits
