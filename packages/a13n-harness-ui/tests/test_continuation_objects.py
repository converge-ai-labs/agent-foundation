"""Dual-format reads, forward-only writes and checkpoint-exclusive payload ownership."""

from datetime import UTC, datetime

import pytest
from a13n_harness import HarnessState
from a13n_harness_ui.display_history import import_display_history, saved_display_history, with_display_history
from a13n_harness_ui.errors import ObjectIntegrityError
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage import open_local_store
from a13n_harness_ui.storage.continuations import publish_continuation, read_continuation, remove_continuation
from a13n_harness_ui.storage.contracts import Continuation, ReferencedContinuation, StoredContinuation
from a13n_harness_ui.storage.display import DisplayArchive, read_display, seal_display
from a13n_harness_ui.storage.objects import ObjectKind, ObjectRef
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart

pytestmark = pytest.mark.anyio


def checkpoint():
    history = [
        ModelRequest(parts=[UserPromptPart("First"), ToolReturnPart("view", {"z": 1, "a": 2}, "call")]),
        ModelResponse(parts=[TextPart("Answer")]),
        ModelRequest(parts=[UserPromptPart("Second")]),
        ModelResponse(parts=[TextPart("Next")]),
    ]
    return Continuation(
        harness_release="test",
        run_composition=ObjectRef(
            object_kind=ObjectKind.run_composition, object_schema_version="1", logical_digest="a" * 64
        ),
        harness_state=HarnessState.new(message_history=history),
        display=DisplayArchive(tail=import_display_history(history)),
        memory_cursors={"global": "cursor"},
        created_at=datetime.now(UTC),
    )


async def test_legacy_read_migrates_to_separate_payloads_and_preserves_metadata_order(tmp_path):
    value = checkpoint()
    assert value.display is not None
    legacy = StoredContinuation(
        harness_release=value.harness_release,
        run_composition=value.run_composition,
        harness_state=with_display_history(value.harness_state, value.display.tail),
        memory_cursors=value.memory_cursors,
        created_at=value.created_at,
    )
    payload = legacy.model_dump(mode="json") | {"future_metadata": {"retained": True}}
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        old = await store.objects.publish(
            object_kind=ObjectKind.continuation, object_schema_version="1", payload=payload
        )
        restored = await read_continuation(store.objects, old.ref, include_display=True)
        assert saved_display_history(restored.harness_state) is None
        assert restored.display == value.display
        new = await publish_continuation(store.objects, restored)
        manifest = await store.objects.read_model(new, ReferencedContinuation)
        assert manifest.schema_version == "2" and manifest.display is not None
        assert manifest.model_extra == {"future_metadata": {"retained": True}}
        raw = (await store.objects.read(new)).payload
        assert "harness_state" not in raw
        assert (await store.objects.read(manifest.native_state)).payload_codec_version == "2"
        reopened = await read_continuation(store.objects, new, include_display=True)
        assert reopened == restored
        assert reopened.harness_state.message_history_json == value.harness_state.message_history_json
        assert (await store.objects.read(old.ref)).payload == payload


async def test_native_only_read_skips_display_and_cannot_republish_unloaded_history(tmp_path, monkeypatch):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        ref = await publish_continuation(store.objects, checkpoint())
        read = store.objects.read_model
        kinds = []

        async def record(ref, model):
            kinds.append(ref.object_kind)
            return await read(ref, model)

        monkeypatch.setattr(store.objects, "read_model", record)
        value = await read_continuation(store.objects, ref)
        assert kinds == [ObjectKind.continuation, ObjectKind.continuation_state]
        assert not value.display_loaded
        with pytest.raises(ValueError, match="Load display"):
            await publish_continuation(store.objects, value)


async def test_payloads_are_exclusive_but_sealed_segments_survive_release_and_fork(tmp_path):
    value = checkpoint()
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        archive = await seal_display(store.objects, value.display)
        value = value.model_copy(update={"display": archive})
        first = await publish_continuation(store.objects, value)
        second = await publish_continuation(store.objects, value)
        fork = await publish_continuation(
            store.objects, value.model_copy(update={"harness_state": value.harness_state.fork()})
        )
        manifests = [await store.objects.read_model(ref, ReferencedContinuation) for ref in (first, second, fork)]
        assert len({item.native_state for item in manifests}) == 3
        assert len({item.display for item in manifests}) == 3
        await remove_continuation(store.objects, second)
        refs = await store.objects.references()
        assert second not in refs and manifests[1].native_state not in refs and manifests[1].display not in refs
        for ref in (first, fork):
            kept = await read_continuation(store.objects, ref, include_display=True)
            assert (await read_display(store.objects, kept.display)).items == (
                await read_display(store.objects, archive)
            ).items
        assert archive.prefix in refs


@pytest.mark.parametrize(
    "kind", [ObjectKind.continuation_state, ObjectKind.continuation_display, ObjectKind.continuation]
)
async def test_partial_publication_does_not_touch_previous_checkpoint(tmp_path, monkeypatch, kind):
    value = checkpoint()
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        old = await publish_continuation(store.objects, value)
        publish = store.objects.publish_model

        async def fail(**kwargs):
            if kwargs["object_kind"] == kind:
                raise OSError("injected")
            return await publish(**kwargs)

        monkeypatch.setattr(store.objects, "publish_model", fail)
        with pytest.raises(OSError, match="injected"):
            await publish_continuation(store.objects, value)
        assert await read_continuation(store.objects, old, include_display=True) == value


async def test_wrong_payload_owner_and_unknown_format_are_rejected(tmp_path):
    async with open_local_store(StorageSettings(data_root=tmp_path)) as store:
        first = await publish_continuation(store.objects, checkpoint())
        manifest = await store.objects.read_model(first, ReferencedContinuation)
        bad = await store.objects.publish_model(
            object_kind=ObjectKind.continuation, value=manifest.model_copy(update={"checkpoint_id": "other"})
        )
        with pytest.raises(ObjectIntegrityError):
            await read_continuation(store.objects, bad.ref)
        unsupported = await store.objects.publish_model(
            object_kind=ObjectKind.continuation, value=manifest.model_copy(update={"schema_version": "3"})
        )
        with pytest.raises(ObjectIntegrityError):
            await read_continuation(store.objects, unsupported.ref)
