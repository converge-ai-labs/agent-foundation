"""Referenced history preserves originals while admission reads only the suffix."""

from datetime import UTC, datetime

import pytest
from a13n_harness import HarnessState
from a13n_harness_ui.display_history import DisplayHistoryCollector, import_display_history
from a13n_harness_ui.display_projection import display_entries, display_turns
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import open_local_store
from a13n_harness_ui.storage.continuations import publish_continuation, read_continuation
from a13n_harness_ui.storage.contracts import Continuation
from a13n_harness_ui.storage.display import DisplayArchive, read_display, seal_display
from a13n_harness_ui.storage.objects import ObjectKind, ObjectRef
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

pytestmark = pytest.mark.anyio


def history(count):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return import_display_history(
        [
            message
            for index in range(count)
            for message in (
                ModelRequest(parts=[UserPromptPart(f"input {index}")], timestamp=now),
                ModelResponse(parts=[TextPart(f"answer {index}")], timestamp=now),
            )
        ]
    )


@pytest.mark.parametrize("count", [50, 500])
async def test_archive_reopens_without_reading_prefix_and_rebuilds_exact_coordinates(tmp_path, monkeypatch, count):
    original = history(count)
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        archive = await seal_display(store.objects, DisplayArchive(tail=original))
        assert archive.entry_offset == 2 * (count - 1)
        assert archive.turn_offset == count - 1
        assert len(archive.tail.items) == 2
        restored = await read_display(store.objects, archive)
        assert restored.items == original.items
        assert display_entries(restored) == display_entries(original)
        assert display_turns(restored) == display_turns(original)
        value = Continuation(
            harness_release="test",
            created_at=datetime.now(UTC),
            run_composition=ObjectRef(
                object_kind=ObjectKind.run_composition, object_schema_version="1", logical_digest="a" * 64
            ),
            harness_state=HarnessState.new(),
            display=archive,
        )
        selected = await publish_continuation(store.objects, value)
        read = store.objects.read_model
        seen = []

        async def record(ref, model):
            seen.append(ref.object_kind)
            return await read(ref, model)

        monkeypatch.setattr(store.objects, "read_model", record)
        reopened = await read_continuation(store.objects, selected, include_display=True)
        assert reopened.display is not None
        assert await seal_display(store.objects, reopened.display) == archive
        collector = DisplayHistoryCollector((), reopened.display.tail)
        assert len(collector.capture().items) == 2
        assert seen == [ObjectKind.continuation, ObjectKind.continuation_state, ObjectKind.continuation_display]
        assert len(reopened.model_dump_json()) < 5000


async def test_failed_prefix_publication_does_not_mutate_source(tmp_path, monkeypatch):
    original = DisplayArchive(tail=history(300))
    encoded = original.model_dump_json()
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        publish = store.objects.publish_model
        calls = 0

        async def fail(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected publication failure")
            return await publish(**kwargs)

        monkeypatch.setattr(store.objects, "publish_model", fail)
        with pytest.raises(OSError, match="injected"):
            await seal_display(store.objects, original)
        assert original.model_dump_json() == encoded
        monkeypatch.setattr(store.objects, "publish_model", publish)
        archived = await seal_display(store.objects, original)
        assert (await read_display(store.objects, archived)).items == original.tail.items


async def test_turn_content_revision_matches_sealed_and_rebuilt_entries(tmp_path):
    from a13n_harness_ui.storage import Thread
    from a13n_harness_ui.surfaces import TranscriptTurn
    from a13n_harness_ui.thread_projection import build_thread_inspection

    state = HarnessState.new()
    thread = Thread.model_construct(thread_id=state.thread_id, continuation=None)
    original = history(3)
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        archive = await seal_display(store.objects, DisplayArchive(tail=original))
        stored = Continuation(
            harness_release="test",
            created_at=datetime.now(UTC),
            run_composition=ObjectRef(
                object_kind=ObjectKind.run_composition, object_schema_version="1", logical_digest="a" * 64
            ),
            harness_state=state,
            display=archive,
        )
        suffix = build_thread_inspection(thread, stored)
        full = build_thread_inspection(thread, stored.model_copy(update={"display": DisplayArchive(tail=original)}))
        assert suffix.turns[-1].value_json == full.turns[-1].value_json
        before = TranscriptTurn.model_validate_json(suffix.turns[-1].value_json)
        changed_entry = archive.tail.items[-1].content["entry"]
        changed_entry = {**changed_entry, "parts": [{**changed_entry["parts"][0], "text": "corrected"}]}
        changed = archive.tail.model_copy(
            update={
                "items": (
                    *archive.tail.items[:-1],
                    archive.tail.items[-1].model_copy(
                        update={"content": {**archive.tail.items[-1].content, "entry": changed_entry}}
                    ),
                )
            }
        )
        updated = build_thread_inspection(
            thread, stored.model_copy(update={"display": archive.model_copy(update={"tail": changed})})
        )
        after = TranscriptTurn.model_validate_json(updated.turns[-1].value_json)
        assert before.content_revision and before.content_revision != after.content_revision
