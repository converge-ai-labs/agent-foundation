from __future__ import annotations

import json

import pytest
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.errors import RunCoordinationError, ThreadError
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from a13n_harness_ui.surfaces import (
    MessageEditSelection,
    RootOperationStatus,
    SkillReference,
    ThreadMetadataMutation,
    ThreadMetadataPatch,
)
from a13n_harness_ui.thread_files import AttachmentUpload, ComposerAttachmentReference, ComposerInput
from anyio import Event, fail_after
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_app import _settings, _write_configuration
from .test_sharing_webui import create_link, shared_server

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("migrated_databases")]


@pytest.fixture
def model(monkeypatch):
    def install(stream):
        async def resolve(self, context, model_id):
            return FunctionModel(stream_function=stream)

        monkeypatch.setattr(HarnessUiModelResolver, "__call__", resolve)

    return install


def settings(tmp_path):
    return _settings(tmp_path / "data").model_copy(update={"pricing_auto_update": False})


async def send(app, thread_id, text, status=RootOperationStatus.completed, **kwargs):
    prompt = ComposerInput((text,)) if isinstance(text, str) else text
    receipt = await app.submit_thread(thread_id=thread_id, prompt=prompt, **kwargs)
    assert (await app.wait_root_operation(receipt.receipt_id)).status is status
    return receipt


async def selection_for(app, thread_id, position):
    transcript = await app.get_thread_transcript(thread_id=thread_id)
    key = transcript.turns[position].turn_id
    points = await app.branch_points(thread_id)
    assert points.points[key].edit
    return MessageEditSelection(expected_source=points.edit_source, turn_key=key)


@pytest.mark.parametrize("position", [0, 1, 2])
async def test_idle_edit_replaces_selected_suffix_in_place(tmp_path, monkeypatch, model, position):
    calls = []

    async def stream(messages, info):
        calls.append(ModelMessagesTypeAdapter.dump_json(messages).decode())
        yield f"Reply {len(calls)}"

    model(stream)
    async with open_harness_ui_app(settings(tmp_path), configuration_path=_write_configuration(tmp_path)) as app:
        thread = await app.create_thread(title="Original title")
        thread_id = thread.thread_id
        for text in ("Prompt one", "Prompt two", "Prompt three"):
            await send(app, thread_id, text)
        selection = await selection_for(app, thread_id, position)
        original = await app._threads.get(thread_id)
        assert original.completion and original.last_execution
        prior = await app._store.read_continuation(thread_id, original.continuation)
        published = []
        select = app._threads.select_message_rewind

        async def inspect_rewind(rewind, composition):
            selected = await select(rewind, composition)
            assert selected.completion is None and selected.last_execution is None
            stored = await app._store.read_continuation(thread_id, selected.continuation, include_display=True)
            assert len(stored.harness_state.message_history) == position * 2
            assert stored.harness_state.environment_states == prior.harness_state.environment_states
            assert not stored.deferred_requests
            history = await app.get_thread_transcript(thread_id=thread_id)
            assert len(history.turns) == position
            if not position:
                assert not history.entries and not stored.excerpt.first_input
            published.append(selected.continuation.logical_digest)
            return selected

        monkeypatch.setattr(app._threads, "select_message_rewind", inspect_rewind)
        edited = await app.edit_thread_message(
            thread_id=thread_id, selection=selection, prompt=ComposerInput(("Replacement",))
        )
        assert edited.receipt.thread_id == thread_id
        assert edited.continuation_id == published[0]
        assert (await app.wait_root_operation(edited.receipt.receipt_id)).status is RootOperationStatus.completed
        detail = await app.get_thread(thread_id)
        assert detail.thread.title == "Original title"
        assert (await app.list_threads()).total == 1
        assert "Replacement" in calls[-1]
        history = await app.get_thread_transcript(thread_id=thread_id)
        for index, text in enumerate(("Prompt one", "Prompt two", "Prompt three")):
            assert (text in calls[-1]) is (index < position)
        assert [turn.preview for turn in history.turns][-1] == "Replacement"
        assert len(history.turns) == position + 1
        with pytest.raises(ThreadError, match="history changed"):
            await app.edit_thread_message(
                thread_id=thread_id, selection=selection, prompt=ComposerInput(("Stale retry",))
            )


@pytest.mark.parametrize("target", ["earlier", "active", "timeout"])
async def test_edit_active_prompt_waits_for_selected_run_to_stop(tmp_path, monkeypatch, model, target):
    calls = []
    started, release = Event(), Event()

    async def stream(messages, info):
        calls.append(ModelMessagesTypeAdapter.dump_json(messages).decode())
        if len(calls) == 2:
            started.set()
            await release.wait()
        yield f"Reply {len(calls)}"

    model(stream)
    async with open_harness_ui_app(settings(tmp_path), configuration_path=_write_configuration(tmp_path)) as app:
        thread_id = (await app.create_thread()).thread_id
        await send(app, thread_id, "Earlier prompt")
        if target == "timeout":
            persist = app._root_runs._persist_execution

            async def hold_settlement(operation):
                if operation.completed_at is not None:
                    await release.wait()
                await persist(operation)

            # Hold the receipt's settlement even if the model accepts Stop promptly.
            monkeypatch.setattr(app._root_runs, "_persist_execution", hold_settlement)
        source_id = "input_" + "1" * 32
        active = await app.submit_thread(
            thread_id=thread_id, prompt=ComposerInput(("Running prompt",), source_id=source_id)
        )
        try:
            with fail_after(10):
                await started.wait()
            if target == "earlier":
                selection = await selection_for(app, thread_id, 0)
            else:
                points = await app.branch_points(thread_id)
                assert points.points[source_id].edit
                selection = MessageEditSelection(expected_source=points.edit_source, turn_key=source_id)
            assert selection.expected_source == active.receipt_id
            arguments = dict(thread_id=thread_id, selection=selection, prompt=ComposerInput(("Replacement",)))
            if target == "timeout":
                monkeypatch.setattr("a13n_harness_ui.root_run._EDIT_STOP_SECONDS", 0.05)

                async def unexpected_rewind(*args, **kwargs):
                    pytest.fail("A stop timeout must not select an edit rewind")

                monkeypatch.setattr(app._threads, "select_message_rewind", unexpected_rewind)
                with pytest.raises(RunCoordinationError) as error:
                    await app.edit_thread_message(**arguments)
                assert error.value.code == "thread_edit_stop_timeout"
                # The original Run may still publish one of its own checkpoints.
                after = await app._threads.get(thread_id)
                assert after.last_execution is not None
                assert after.last_execution.execution_id == active.receipt_id
                assert thread_id in await app._root_runs.active_thread_ids()
                assert len(calls) == 2
                return
            edited = await app.edit_thread_message(**arguments)
            assert (await app.wait_root_operation(active.receipt_id)).status is RootOperationStatus.cancelled
            assert (await app.wait_root_operation(edited.receipt.receipt_id)).status is RootOperationStatus.completed
            assert ("Earlier prompt" in calls[-1]) is (target == "active")
            assert "Running prompt" not in calls[-1] and "Replacement" in calls[-1]
        finally:
            release.set()


async def test_edit_after_context_clear_keeps_display_prefix_separate_from_model_history(tmp_path, model):
    calls = []

    async def stream(messages, info):
        calls.append(ModelMessagesTypeAdapter.dump_json(messages).decode())
        yield f"Reply {len(calls)}"

    model(stream)
    async with open_harness_ui_app(settings(tmp_path), configuration_path=_write_configuration(tmp_path)) as app:
        thread_id = (await app.create_thread()).thread_id
        await send(app, thread_id, "Retained display")
        previous = await app.get_thread(thread_id)
        await app.clear_thread_context(thread_id=thread_id, expected_continuation_id=previous.continuation_id)
        await send(app, thread_id, "Discard this")
        selection = await selection_for(app, thread_id, -1)
        edited = await app.edit_thread_message(
            thread_id=thread_id, selection=selection, prompt=ComposerInput(("Replacement",))
        )
        assert (await app.wait_root_operation(edited.receipt.receipt_id)).status is RootOperationStatus.completed
        assert "Retained display" not in calls[-1] and "Reply 1" not in calls[-1]
        history = await app.get_thread_transcript(thread_id=thread_id)
        assert [turn.preview for turn in history.turns] == ["Retained display", "Replacement"]
        assert history.turns[0].output_preview == "Reply 1"
        assert (await app._threads.get(thread_id)).excerpt.first_input == "Retained display"


async def test_edit_recovers_suspended_turn_without_resolving_discarded_decision(tmp_path, model):
    calls = []

    async def stream(messages, info):
        calls.append(ModelMessagesTypeAdapter.dump_json(messages).decode())
        if len(calls) == 1:
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
        else:
            yield "Replacement answer"

    model(stream)
    async with open_harness_ui_app(
        settings(tmp_path), configuration_path=_write_configuration(tmp_path), host_mode="webui"
    ) as app:
        thread_id = (await app.create_thread()).thread_id
        await send(app, thread_id, "Pending question", RootOperationStatus.suspended)
        assert (await app.get_thread(thread_id)).deferred_requests
        selection = await selection_for(app, thread_id, 0)
        edited = await app.edit_thread_message(
            thread_id=thread_id, selection=selection, prompt=ComposerInput(("Replacement",))
        )
        assert (await app.wait_root_operation(edited.receipt.receipt_id)).status is RootOperationStatus.completed
        assert "Choose a path" not in calls[-1] and "Pending question" not in calls[-1]
        assert not (await app.get_thread(thread_id)).deferred_requests
        assert thread_id not in app._root_runs._interaction_waits


async def test_edit_http_keeps_retained_attachments_and_skill_references(tmp_path, model):
    calls = []

    async def stream(messages, info):
        calls.append(ModelMessagesTypeAdapter.dump_json(messages).decode())
        yield "Saved answer"

    model(stream)
    path = _write_configuration(tmp_path)
    agent = tmp_path / "agents/assistant.yaml"
    agent.write_text(agent.read_text() + "capabilities:\n  - capability: skills\n")
    owner = {"Authorization": "Bearer " + "owner-test-key"}
    async with shared_server(tmp_path, configuration=path) as (_, app, client):
        thread_id = (await app.create_thread()).thread_id
        retained = []
        for name in ("keep.txt", "remove.txt"):
            retained.append(await app._thread_files.stage(thread_id, AttachmentUpload(name, b"Retained bytes")))
        catalog = await app.skill_catalog(thread_id=thread_id)
        item = next(item for item in catalog.items if item.name == "harness-ui-configuration")
        refs = (SkillReference(catalog_id=catalog.catalog_id, item_id=item.item_id, name=item.name),)
        await send(
            app,
            thread_id,
            ComposerInput(
                (
                    "Use $harness-ui-configuration ",
                    *(ComposerAttachmentReference(a.attachment_id) for a in retained),
                    " now",
                )
            ),
            skill_references=refs,
            input_surface="webui",
        )
        selection = await selection_for(app, thread_id, 0)
        prefix, guest = await create_link(client, "thread", thread_id, permission="prompt")
        payload = dict(
            selection=selection.model_dump(),
            parts=["Apply $harness-ui-configuration ", {"attachment_id": retained[0].attachment_id}, " now"],
            skill_references=[ref.model_dump() for ref in refs],
        )
        endpoint = f"/api/threads/{thread_id}/edit-message"
        assert (await client.post(endpoint, headers=guest, json=payload)).status_code == 401
        assert (await client.post(prefix + "/edit-message", headers=guest, json=payload)).status_code == 405
        result = await client.post(endpoint, headers=owner, json=payload)
        assert result.status_code == 200, result.text
        receipt = result.json()["receipt"]
        assert receipt["thread_id"] == thread_id
        assert (await app.wait_root_operation(receipt["receipt_id"])).status is RootOperationStatus.completed
        assert "<skill-selection" in calls[-1] and "Apply $harness-ui-configuration" in calls[-1]
        assert "keep.txt" in calls[-1] and "remove.txt" not in calls[-1]
        assert (await app._thread_files.read(thread_id, retained[1].attachment_id))[1] == b"Retained bytes"


async def test_edit_rejects_archived_and_nonordinary_threads(tmp_path):
    async with open_harness_ui_app(settings(tmp_path), configuration_path=_write_configuration(tmp_path)) as app:
        coordinator = await app.create_thread(coordinator=True)
        worker = await app.create_thread(coordinator_thread_id=coordinator.thread_id)
        source = await app._threads.get(worker.thread_id)
        child = await app._store.threads.create(
            thread_id="thread_child",
            parent_thread_id=worker.thread_id,
            configuration=source.configuration,
            initial_state=source.initial_state,
        )
        archived = await app.create_thread()
        await app.update_thread_metadata(
            thread_id=archived.thread_id,
            mutation=ThreadMetadataMutation(
                expected_version=archived.metadata_version, patch=ThreadMetadataPatch(archived=True)
            ),
        )
        from a13n_harness_ui.bot_models import BOT_THREAD_ID

        for thread_id in (coordinator.thread_id, worker.thread_id, child.thread_id, archived.thread_id, BOT_THREAD_ID):
            with pytest.raises(ThreadError):
                await app.edit_thread_message(
                    thread_id=thread_id,
                    selection=MessageEditSelection(expected_source="a" * 64, turn_key="not available"),
                    prompt=ComposerInput(("Replacement",)),
                )


async def test_active_edit_cannot_restore_context_cleared_by_another_app(tmp_path, monkeypatch, model):
    started, release = Event(), Event()
    calls = 0

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls == 2:
            started.set()
            await release.wait()
        yield "Answer"

    model(stream)
    path = _write_configuration(tmp_path)
    async with (
        open_harness_ui_app(settings(tmp_path), configuration_path=path) as app,
        open_harness_ui_app(settings(tmp_path), configuration_path=path) as other,
    ):
        thread_id = (await app.create_thread()).thread_id
        await send(app, thread_id, "Earlier context")
        source_id = "input_" + "2" * 32
        active = await app.submit_thread(thread_id=thread_id, prompt=ComposerInput(("Running",), source_id=source_id))
        cleared = []
        stop = app._root_runs.stop_for_message_edit

        async def stop_then_clear(receipt_id):
            checkpoint = await stop(receipt_id)
            assert checkpoint is not None
            result = await other.clear_thread_context(
                thread_id=thread_id, expected_continuation_id=checkpoint.logical_digest
            )
            cleared.append(result.continuation_id)
            return checkpoint

        monkeypatch.setattr(app._root_runs, "stop_for_message_edit", stop_then_clear)
        try:
            with fail_after(10):
                await started.wait()
            points = await app.branch_points(thread_id)
            assert points.edit_source == active.receipt_id
            with pytest.raises(ThreadError, match="history changed"):
                await app.edit_thread_message(
                    thread_id=thread_id,
                    selection=MessageEditSelection(expected_source=points.edit_source, turn_key=source_id),
                    prompt=ComposerInput(("Replacement",)),
                )
            assert calls == 2 and (await app.get_thread(thread_id)).continuation_id == cleared[0]
        finally:
            release.set()


@pytest.mark.parametrize("status", [RootOperationStatus.failed, RootOperationStatus.cancelled])
async def test_edit_replaces_a_failed_or_cancelled_input(tmp_path, model, status):
    started, release = Event(), Event()
    calls = 0

    async def stream(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield "Discard this partial output"
            started.set()
            if status is RootOperationStatus.failed:
                raise RuntimeError("Fixture failure after partial output")
            await release.wait()
        else:
            yield "Replacement answer"

    model(stream)
    async with open_harness_ui_app(settings(tmp_path), configuration_path=_write_configuration(tmp_path)) as app:
        thread_id = (await app.create_thread()).thread_id
        receipt = await app.submit_thread(thread_id=thread_id, prompt=ComposerInput(("Discarded input",)))
        try:
            with fail_after(10):
                await started.wait()
            if status is RootOperationStatus.cancelled:
                assert (await app.cancel_root_operation(receipt.receipt_id)).accepted
            assert (await app.wait_root_operation(receipt.receipt_id)).status is status
            selection = await selection_for(app, thread_id, 0)
            edited = await app.edit_thread_message(
                thread_id=thread_id, selection=selection, prompt=ComposerInput(("Replacement",))
            )
            assert (await app.wait_root_operation(edited.receipt.receipt_id)).status is RootOperationStatus.completed
            history = await app.get_thread_transcript(thread_id=thread_id)
            assert [turn.preview for turn in history.turns] == ["Replacement"]
            assert not any(entry.failures for entry in history.entries)
        finally:
            release.set()
