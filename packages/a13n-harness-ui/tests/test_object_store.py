from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import zstandard
from a13n_harness_ui.errors import ObjectIntegrityError, StoreIntegrityError
from a13n_harness_ui.settings import StorageSettings
from a13n_harness_ui.storage.layout import StorageLayout
from a13n_harness_ui.storage.objects import ImmutableObjectStore, ObjectKind, ObjectRef
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


def _object_store(
    root: Path,
    *,
    max_object_bytes: int | None = None,
) -> tuple[ImmutableObjectStore, StorageLayout]:
    settings = StorageSettings(data_root=root)
    if max_object_bytes is not None:
        settings = StorageSettings(data_root=root, max_object_bytes=max_object_bytes)
    layout = StorageLayout.from_root(settings.data_root)
    layout.prepare()
    return ImmutableObjectStore(layout, settings, producer_release="test-release"), layout


async def test_object_round_trip_is_canonical_and_reuses_exact_content(tmp_path: Path) -> None:
    store, layout = _object_store(tmp_path)
    created_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    first = await store.publish(
        object_kind=ObjectKind.configuration_generation,
        object_schema_version="1",
        payload={"unicode": "你好", "ordered": {"b": 2, "a": 1}},
        created_at=created_at,
    )
    second = await store.publish(
        object_kind=ObjectKind.configuration_generation,
        object_schema_version="1",
        payload={"ordered": {"a": 1, "b": 2}, "unicode": "你好"},
        created_at=created_at.replace(second=6),
    )

    assert second == first
    assert await store.read(first.ref) == first
    paths = list(layout.objects.rglob("*.json.zst"))
    assert len(paths) == 1
    raw = zstandard.ZstdDecompressor().decompress(paths[0].read_bytes())
    assert (
        raw
        == json.dumps(
            json.loads(raw),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    if os.name != "nt":
        assert paths[0].stat().st_mode & 0o777 == 0o600


async def test_object_publish_uses_level_one_and_reuses_existing_level_three_frames(tmp_path: Path) -> None:
    store, layout = _object_store(tmp_path)
    payload = {
        "records": [
            {"id": index, "value": f"{index * index:016x}", "summary": f"Checkpoint for worker {index % 17}"}
            for index in range(1024)
        ]
    }
    envelope = await store.publish(
        object_kind=ObjectKind.continuation,
        object_schema_version="1",
        payload=payload,
    )
    path = next(layout.objects.rglob("*.json.zst"))
    encoded = path.read_bytes()
    raw = zstandard.ZstdDecompressor().decompress(encoded)
    assert encoded == zstandard.ZstdCompressor(level=1, write_checksum=True).compress(raw)
    assert await store.read(envelope.ref) == envelope

    # Previously published level-3 objects retain their identity and exact bytes.
    previous_encoding = zstandard.ZstdCompressor(level=3, write_checksum=True).compress(raw)
    path.write_bytes(previous_encoding)
    assert await store.read(envelope.ref) == envelope
    republished = await store.publish(
        object_kind=ObjectKind.continuation,
        object_schema_version="1",
        payload=payload,
    )
    assert republished == envelope
    assert path.read_bytes() == previous_encoding


async def test_object_publish_rejects_invalid_or_excessive_payloads(tmp_path: Path) -> None:
    store, _ = _object_store(tmp_path, max_object_bytes=1024)

    with pytest.raises(StoreIntegrityError) as non_finite:
        await store.publish(
            object_kind=ObjectKind.configuration_generation,
            object_schema_version="1",
            payload={"value": float("nan")},
        )
    assert non_finite.value.code == "object_payload_invalid"

    with pytest.raises(StoreIntegrityError) as excessive:
        await store.publish(
            object_kind=ObjectKind.configuration_generation,
            object_schema_version="1",
            payload={"value": "x" * 2048},
        )
    assert excessive.value.code == "object_too_large"
    assert excessive.value.details["actual_bytes"] > 2048
    assert excessive.value.details["max_object_bytes"] == 1024
    assert "process.max_object_bytes" in str(excessive.value)
    assert not list(tmp_path.rglob("*.json.zst"))
    assert not list((tmp_path / "staging").iterdir())

    with pytest.raises(StoreIntegrityError) as unknown_codec:
        await store.publish(
            object_kind=ObjectKind.configuration_generation,
            object_schema_version="1",
            payload={},
            payload_codec_version="2",
        )
    assert unknown_codec.value.code == "object_payload_invalid"

    with pytest.raises(ValidationError):
        ObjectRef(
            object_kind=ObjectKind.configuration_generation,
            object_schema_version="2",
            logical_digest="0" * 64,
        )


async def test_object_read_rejects_corruption_truncation_and_trailing_data(tmp_path: Path) -> None:
    store, layout = _object_store(tmp_path)
    envelope = await store.publish(
        object_kind=ObjectKind.continuation,
        object_schema_version="1",
        payload={"step": 1},
    )
    path = next(layout.objects.rglob("*.json.zst"))
    original = path.read_bytes()

    decoded = json.loads(zstandard.ZstdDecompressor().decompress(original))
    decoded["payload"]["step"] = 2
    path.write_bytes(
        zstandard.ZstdCompressor(write_checksum=True).compress(
            json.dumps(decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        )
    )
    with pytest.raises(ObjectIntegrityError) as digest_error:
        await store.read(envelope.ref)
    assert digest_error.value.code == "object_digest_mismatch"

    decoded_original = zstandard.ZstdDecompressor().decompress(original)
    path.write_bytes(zstandard.ZstdCompressor(write_checksum=False).compress(decoded_original))
    with pytest.raises(ObjectIntegrityError) as checksum_error:
        await store.read(envelope.ref)
    assert checksum_error.value.code == "object_checksum_missing"

    path.write_bytes(original[:-3])
    with pytest.raises(ObjectIntegrityError) as truncated_error:
        await store.read(envelope.ref)
    assert truncated_error.value.code == "object_unreadable"

    path.write_bytes(original + b"trailing")
    with pytest.raises(ObjectIntegrityError) as trailing_error:
        await store.read(envelope.ref)
    assert trailing_error.value.code == "object_unreadable"

    path.unlink()
    with pytest.raises(ObjectIntegrityError) as missing_error:
        await store.read(envelope.ref)
    assert missing_error.value.code == "object_unreadable"


async def test_payload_reader_tolerates_nested_unknown_fields_without_rewriting_bytes(tmp_path):
    from pydantic import ConfigDict, create_model

    entry = create_model("StoredEntry", __config__=ConfigDict(extra="forbid"), count=(int, ...))
    payload_type = create_model("StoredPayload", __config__=ConfigDict(extra="forbid"), entries=(list[entry], ...))
    payload = {"retired": True, "entries": [{"count": 3, "future": {"value": [1, None]}}]}
    store, layout = _object_store(tmp_path)
    envelope = await store.publish(object_kind=ObjectKind.child_checkpoint, object_schema_version="1", payload=payload)
    path = next(layout.objects.rglob("*.json.zst"))
    original = path.read_bytes()
    restored = await store.read_model(envelope.ref, payload_type)
    assert restored.entries[0].count == 3
    assert restored.model_extra == {"retired": True}
    assert restored.entries[0].model_extra == {"future": {"value": [1, None]}}
    assert await store.read(envelope.ref) == envelope
    assert path.read_bytes() == original
    # Source/request validation is independent of historical object decoding.
    with pytest.raises(ValidationError):
        payload_type.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("entry", [{"future": True}, {"count": "3", "future": True}])
async def test_tolerant_payload_reader_still_rejects_missing_or_invalid_known_fields(tmp_path, entry):
    from pydantic import ConfigDict, create_model

    payload_type = create_model("StoredCount", __config__=ConfigDict(extra="forbid"), count=(int, ...))
    store, _ = _object_store(tmp_path)
    envelope = await store.publish(object_kind=ObjectKind.child_checkpoint, object_schema_version="1", payload=entry)
    with pytest.raises(ObjectIntegrityError) as error:
        await store.read_model(envelope.ref, payload_type)
    assert error.value.code == "object_payload_incompatible"


async def test_payload_validation_logs_identity_and_bounded_fields_without_private_values(tmp_path, caplog):
    from pydantic import ConfigDict, create_model

    entry = create_model("DiagnosticEntry", context_window_tokens=(int, ...))
    payload_type = create_model(
        "DiagnosticPayload", __config__=ConfigDict(extra="forbid"), models=(dict[str, entry], ...)
    )
    store, _ = _object_store(tmp_path)
    envelope = await store.publish(
        object_kind=ObjectKind.configuration_generation,
        object_schema_version="1",
        payload={"models": {f"private-key-{i}": {"context_window_tokens": "/private/config"} for i in range(12)}},
    )
    with pytest.raises(ObjectIntegrityError) as error:
        await store.read_model(envelope.ref, payload_type)
    assert error.value.code == "object_payload_incompatible"
    record = next(record for record in caplog.records if record.name == "a13n_harness_ui.storage.objects")
    assert record.object_kind == "configuration-generation"
    assert record.object_schema_version == "1"
    assert record.object_digest == envelope.logical_digest
    assert record.payload_model == "DiagnosticPayload"
    assert record.validation_error_count == 12
    assert len(record.validation_errors) == 8
    assert record.validation_errors[0] == {"location": "models.*.context_window_tokens", "type": "int_type"}
    assert "private" not in repr(record.__dict__)
    assert "private" not in repr(error.value.details)
    assert await store.read(envelope.ref) == envelope


async def test_object_read_checks_declared_size_before_decompression(tmp_path: Path) -> None:
    writer, layout = _object_store(tmp_path, max_object_bytes=4096)
    envelope = await writer.publish(
        object_kind=ObjectKind.environment_state,
        object_schema_version="1",
        payload={"value": "x" * 2048},
    )
    reader = ImmutableObjectStore(
        layout,
        StorageSettings(data_root=tmp_path, max_object_bytes=1024),
        producer_release="test-release",
    )

    with pytest.raises(ObjectIntegrityError) as excessive:
        await reader.read(envelope.ref)
    assert excessive.value.code == "object_too_large"
    assert excessive.value.details["actual_bytes"] > 2048
    assert excessive.value.details["max_object_bytes"] == 1024
    assert "process.max_object_bytes" in str(excessive.value)
    assert await writer.read(envelope.ref) == envelope


async def test_default_store_round_trips_continuation_above_previous_limit(tmp_path: Path) -> None:
    store, _ = _object_store(tmp_path)
    payload = {"display_history": "x" * (64 * 1024 * 1024)}
    envelope = await store.publish(
        object_kind=ObjectKind.continuation,
        object_schema_version="1",
        payload=payload,
    )
    reopened, _ = _object_store(tmp_path)
    assert (await reopened.read(envelope.ref)).payload == payload
