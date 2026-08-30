from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import zstandard
from a13n_ui.errors import ObjectIntegrityError, StoreIntegrityError
from a13n_ui.settings import StorageSettings
from a13n_ui.storage.layout import StorageLayout
from a13n_ui.storage.objects import ImmutableObjectStore, ObjectKind, ObjectRef
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


def _object_store(
    root: Path,
    *,
    max_object_bytes: int = 64 * 1024 * 1024,
) -> tuple[ImmutableObjectStore, StorageLayout]:
    settings = StorageSettings(data_root=root, max_object_bytes=max_object_bytes)
    layout = StorageLayout.from_root(settings.data_root)
    layout.prepare()
    return ImmutableObjectStore(layout, settings, producer_release="test-release"), layout


async def test_object_round_trip_is_canonical_and_reuses_exact_content(tmp_path: Path) -> None:
    store, layout = _object_store(tmp_path)
    created_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    first = await store.publish(
        object_kind=ObjectKind.agent_snapshot,
        object_schema_version="1",
        payload={"unicode": "你好", "ordered": {"b": 2, "a": 1}},
        created_at=created_at,
    )
    second = await store.publish(
        object_kind=ObjectKind.agent_snapshot,
        object_schema_version="1",
        payload={"ordered": {"a": 1, "b": 2}, "unicode": "你好"},
        created_at=created_at,
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


async def test_object_publish_rejects_invalid_or_excessive_payloads(tmp_path: Path) -> None:
    store, _ = _object_store(tmp_path, max_object_bytes=1024)

    with pytest.raises(StoreIntegrityError) as non_finite:
        await store.publish(
            object_kind=ObjectKind.agent_snapshot,
            object_schema_version="1",
            payload={"value": float("nan")},
        )
    assert non_finite.value.code == "object_payload_invalid"

    with pytest.raises(StoreIntegrityError) as excessive:
        await store.publish(
            object_kind=ObjectKind.agent_snapshot,
            object_schema_version="1",
            payload={"value": "x" * 2048},
        )
    assert excessive.value.code == "object_too_large"

    with pytest.raises(StoreIntegrityError) as unknown_codec:
        await store.publish(
            object_kind=ObjectKind.agent_snapshot,
            object_schema_version="1",
            payload={},
            payload_codec_version="2",
        )
    assert unknown_codec.value.code == "object_payload_invalid"

    with pytest.raises(ValidationError):
        ObjectRef(
            object_kind=ObjectKind.agent_snapshot,
            object_schema_version="2",
            logical_digest="0" * 64,
        )


async def test_object_read_rejects_corruption_truncation_and_trailing_data(tmp_path: Path) -> None:
    store, layout = _object_store(tmp_path)
    envelope = await store.publish(
        object_kind=ObjectKind.harness_state,
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


async def test_object_read_checks_declared_size_before_decompression(tmp_path: Path) -> None:
    writer, layout = _object_store(tmp_path, max_object_bytes=4096)
    envelope = await writer.publish(
        object_kind=ObjectKind.provider_state,
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


async def test_expired_staging_files_are_removed_without_recovery(tmp_path: Path) -> None:
    store, layout = _object_store(tmp_path)
    envelope = await store.publish(
        object_kind=ObjectKind.skill_package,
        object_schema_version="1",
        payload={"files": []},
    )
    first = layout.staging / "00000000000000000000000000000000.json.zst.tmp"
    second = layout.staging / "11111111111111111111111111111111.json.zst.tmp"
    first.write_bytes(b"complete-but-unselected")
    second.write_bytes(b"malformed")
    old = datetime(2020, 1, 1, tzinfo=UTC).timestamp()
    os.utime(first, (old, old))
    os.utime(second, (old, old))

    removed = await store.remove_expired_unregistered(
        {envelope.logical_digest},
        cutoff=datetime.now(UTC),
    )

    assert removed == 2
    assert list(layout.staging.iterdir()) == []
