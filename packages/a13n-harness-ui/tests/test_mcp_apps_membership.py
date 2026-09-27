from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from a13n_harness import HarnessEvent, HarnessState
from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.display_history import DisplayHistory, DisplayHistoryCollector, with_display_history
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.live import HarnessUiLiveHub
from a13n_harness_ui.mcp_apps.models import AppReference, AppSnapshot
from a13n_harness_ui.mcp_apps.snapshots import METADATA_KEY
from a13n_harness_ui.storage import ObjectKind, ObjectRef, StoredContinuation
from a13n_harness_ui.storage.contracts import ThreadReadModel
from a13n_stream_protocol import HarnessAguiObserver
from ag_ui.core import CustomEvent
from pydantic_ai.messages import ModelRequest, PartStartEvent, TextPart, ToolReturnPart, UserPromptPart

from .test_app import _settings, _write_configuration

pytestmark = pytest.mark.anyio


def _event(reference):
    return CustomEvent(
        name=METADATA_KEY,
        value={
            "run_id": reference.run_id,
            "event": {"tool_call_id": reference.tool_call_id, "apps": [reference.model_dump(mode="json")]},
        },
    )


async def _original(app, thread_id, *, count=1):
    original = AppSnapshot(
        connection_generation="connection-original",
        app_id="app-original",
        thread_id=thread_id,
        run_id="run-original",
        tool_call_id="call-original",
        server_id="mcp-original",
        tool={"name": "counter"},
        arguments={},
        result={"content": [], "structuredContent": {"count": count}},
    )
    saved = await app._store.objects.publish_model(object_kind=ObjectKind.mcp_app_snapshot, value=original)
    return AppReference(
        app_id=original.app_id,
        thread_id=thread_id,
        run_id=original.run_id,
        tool_call_id=original.tool_call_id,
        server_id=original.server_id,
        tool_name="counter",
        snapshot=saved.ref,
    )


async def _publish(hub, observer, reference, *, sequence=1, apps=True):
    events = observer.observe(
        HarnessEvent(
            thread_id=reference.thread_id,
            run_id=reference.run_id,
            sequence=sequence,
            occurred_at=datetime.now(UTC),
            event=PartStartEvent(index=sequence, part=TextPart(content=f"answer-{sequence}")),
        )
    )
    await hub.publish(
        run_kind="root",
        root_thread_id=reference.thread_id,
        parent_thread_id=None,
        thread_id=reference.thread_id,
        run_id=reference.run_id,
        observer=observer,
        events=events,
        supplements=(_event(reference),) if apps else (),
    )


async def test_public_open_and_activation_reject_orphans_before_connection_acquisition(tmp_path, monkeypatch):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "webui:\n  mcp_apps:\n    enabled: true\n")
    async with open_harness_ui_app(_settings(tmp_path / "state"), configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        reference = await _original(app, thread.thread_id)
        acquire = AsyncMock(side_effect=AssertionError("must reject before connecting"))
        monkeypatch.setattr(app._mcp_apps.connections, "acquire", acquire)
        for method in (app.open_mcp_app, app.activate_mcp_app):
            with pytest.raises(HarnessUiError, match="not in retained"):
                await method(thread.thread_id, reference)
        assert acquire.await_count == 0
        await _publish(app._live_hub, HarnessAguiObserver(), reference)
        assert (await app.open_mcp_app(thread.thread_id, reference)).reference == reference
        substituted = await _original(app, thread.thread_id, count=2)
        with pytest.raises(HarnessUiError, match="not in retained"):
            await app.open_mcp_app(thread.thread_id, substituted)
        with pytest.raises(HarnessUiError, match="another Thread"):
            await app.open_mcp_app(thread.thread_id, reference.model_copy(update={"thread_id": "other"}))


async def test_live_replay_keeps_supplements_ordered_and_membership_outlives_ring_only_until_release():
    hub = HarnessUiLiveHub(ring_size=2)
    reference = AppReference(
        app_id="app-1",
        thread_id="thread-1",
        run_id="run-1",
        tool_call_id="call-1",
        server_id="mcp-1",
        tool_name="counter",
    )
    observer = HarnessAguiObserver()
    await _publish(hub, observer, reference)
    async with hub.subscribe(root_thread_id=reference.thread_id) as subscription:
        captured = subscription.root_stream
        assert captured is not None
        prefix = [event for batch in captured.batches() for event in batch]
        assert prefix[-1].payload["name"] == METADATA_KEY
        assert captured.summary.event_count == observer.event_count + 1
        for sequence in range(2, 10):
            await _publish(hub, observer, reference, sequence=sequence, apps=False)
        assert [event for batch in captured.batches() for event in batch] == prefix
    assert await hub.retains_mcp_app(reference)
    assert not any(event.payload.get("name") == METADATA_KEY for event in await hub.snapshot())
    async with hub.subscribe(root_thread_id=reference.thread_id) as subscription:
        replay = subscription.root_stream
        events = [event for batch in replay.batches() for event in batch]
        assert len(events) == replay.summary.event_count == observer.event_count + 1
        assert [event.index for event in events] == list(range(len(events)))
        assert [event.payload["name"] for event in events if event.event_type == "CUSTOM"] == [METADATA_KEY]
    await hub.finish_root(thread_id=reference.thread_id, run_id=reference.run_id, saved_continuation_id="saved")
    assert not await hub.retains_mcp_app(reference)
    await hub.close()


async def test_saved_display_membership_survives_clear_context_pagination_and_reopen(tmp_path):
    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "webui:\n  mcp_apps:\n    enabled: true\n")
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        thread = await app.create_thread()
        reference = await _original(app, thread.thread_id)
        messages = (
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        "proxy",
                        "done",
                        tool_call_id="outer",
                        metadata={METADATA_KEY: [reference.model_dump(mode="json")]},
                    )
                ]
            ),
            *(ModelRequest(parts=[UserPromptPart(f"later-{i}")]) for i in range(110)),
        )
        state = with_display_history(
            HarnessState.new(thread_id=thread.thread_id),
            DisplayHistoryCollector((), DisplayHistory(messages=messages)).capture(()),
        )
        saved = await app._store.objects.publish_model(
            object_kind=ObjectKind.continuation,
            value=StoredContinuation(
                harness_release="test",
                run_composition=ObjectRef(
                    object_kind=ObjectKind.run_composition, object_schema_version="1", logical_digest="a" * 64
                ),
                harness_state=state,
                created_at=datetime.now(UTC),
            ),
        )
        await _publish(app._live_hub, HarnessAguiObserver(), reference)
        await app._store.threads.select_continuation(
            thread_id=thread.thread_id, expected=None, replacement=saved.ref, read_model=ThreadReadModel()
        )
        await app._live_hub.finish_root(
            thread_id=thread.thread_id, run_id=reference.run_id, saved_continuation_id=saved.ref.logical_digest
        )
        assert (await app.open_mcp_app(thread.thread_id, reference)).reference == reference
        latest = await app.get_thread_transcript(thread_id=thread.thread_id, limit=10)
        assert not any(part.mcp_apps for entry in latest.entries for part in entry.parts)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert (await app.open_mcp_app(thread.thread_id, reference)).reference == reference


async def test_child_membership_pages_resumed_displays_and_reopens_selected_checkpoint(tmp_path, monkeypatch):
    from typing import Any, cast

    from a13n_harness_ui.storage.contracts import CompactChildDisplay, StoredChildCheckpoint
    from a13n_harness_ui.subagent_operator import _ActiveSegment
    from anyio import Event

    root = _write_configuration(tmp_path)
    root.write_text(root.read_text() + "webui:\n  mcp_apps:\n    enabled: true\n")
    settings = _settings(tmp_path / "state")
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        parent = await app.create_thread()
        parent_value = await app._store.threads.get(parent.thread_id)
        for child_id in (*(f"child-noise-{index}" for index in range(100)), "thread_childapp"):
            await app._store.threads.create(
                thread_id=child_id,
                parent_thread_id=parent.thread_id,
                configuration=parent_value.configuration,
                initial_state=parent_value.initial_state,
            )
        composition = ObjectRef(
            object_kind=ObjectKind.run_composition, object_schema_version="1", logical_digest="a" * 64
        )
        for index in range(100):
            await app._store.child_executions.create(
                execution_id=f"noise-{index}",
                parent_thread_id=parent.thread_id,
                child_thread_id=f"child-noise-{index}",
                child_run_id=f"noise-run-{index}",
                run_composition=composition,
            )
        head = await app._store.child_executions.create(
            execution_id="original",
            parent_thread_id=parent.thread_id,
            child_thread_id="thread_childapp",
            child_run_id="run-original",
            run_composition=composition,
        )
        reference = await _original(app, "thread_childapp")
        checkpoint = StoredChildCheckpoint(
            harness_release="test",
            execution_id=head.execution_id,
            child_thread_id=head.child_thread_id,
            child_run_id=head.child_run_id,
            segment_index=0,
            run_composition=composition,
            harness_state=HarnessState.new(thread_id="thread_childapp"),
            display=CompactChildDisplay(),
            terminal=True,
            created_at=datetime.now(UTC),
        )
        saved = await app._store.objects.publish_model(object_kind=ObjectKind.child_checkpoint, value=checkpoint)
        await app._store.child_executions.finish(
            execution_id=head.execution_id, status="succeeded", expected_checkpoint=None, checkpoint=saved.ref
        )
        resumed = await app._store.child_executions.resume(
            previous_execution_id=head.execution_id,
            execution_id="resumed",
            child_run_id="run-resumed",
            run_composition=composition,
        )
        operator = app._subagent_operator
        active = _ActiveSegment(
            execution_id=resumed.execution_id,
            parent_thread_id=parent.thread_id,
            stream=cast(Any, object()),
            done=Event(),
            display=CompactChildDisplay(mcp_apps=(reference,)),
        )
        operator._active[resumed.execution_id] = active
        assert (await app.open_mcp_app("thread_childapp", reference)).reference == reference
        active.display = CompactChildDisplay()
        with pytest.raises(HarnessUiError, match="not in retained"):
            await app.open_mcp_app("thread_childapp", reference)
        active.display = CompactChildDisplay(mcp_apps=(reference,))
        saved = await app._store.objects.publish_model(
            object_kind=ObjectKind.child_checkpoint,
            value=checkpoint.model_copy(
                update={
                    "execution_id": resumed.execution_id,
                    "child_run_id": resumed.child_run_id,
                    "segment_index": resumed.segment_index,
                    "display": active.display,
                }
            ),
        )
        await app._store.child_executions.finish(
            execution_id=resumed.execution_id, status="succeeded", expected_checkpoint=None, checkpoint=saved.ref
        )
        operator._active.pop(resumed.execution_id)
    async with open_harness_ui_app(settings, configuration_path=root, host_mode="webui") as app:
        assert (await app.open_mcp_app("thread_childapp", reference)).reference == reference
