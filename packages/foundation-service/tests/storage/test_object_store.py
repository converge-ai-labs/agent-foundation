from collections.abc import AsyncIterator
from typing import cast

import anyio
import pytest
from converge_foundation_service.storage.object_store import (
    ByteRange,
    InvalidObjectRequest,
    ObjectConflict,
    ObjectNotFound,
    ObjectStore,
)

pytestmark = pytest.mark.anyio


async def _read(store: ObjectStore, key: str, *, byte_range: ByteRange | None = None) -> bytes:
    async with store.open(key, byte_range=byte_range) as reader:
        return b"".join([chunk async for chunk in reader])


async def _source(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


async def test_put_open_and_stat(object_store: ObjectStore) -> None:
    metadata = {"Trace": "trace-1"}

    written = await object_store.put(
        "artifacts/result.json",
        b'{"ok":true}',
        content_type="application/json",
        metadata=metadata,
    )
    metadata["trace"] = "mutated"
    found = await object_store.stat("artifacts/result.json")

    assert await _read(object_store, "artifacts/result.json") == b'{"ok":true}'
    assert found == written
    assert found.metadata == {"trace": "trace-1"}
    assert found.size == 11
    assert found.modified_at.tzinfo is not None
    assert found.version
    with pytest.raises(TypeError):
        cast(dict[str, str], found.metadata)["trace"] = "changed"


async def test_streaming_put_and_ranges(object_store: ObjectStore) -> None:
    await object_store.put("streamed", _source(b"alpha", b"beta", b"gamma"))

    assert await _read(object_store, "streamed", byte_range=ByteRange(2, 8)) == b"phabet"
    assert await _read(object_store, "streamed", byte_range=ByteRange(9)) == b"gamma"


async def test_reader_can_be_closed_before_consuming_the_body(object_store: ObjectStore) -> None:
    await object_store.put("early-close", b"a" * (1024 * 1024))

    async with object_store.open("early-close") as reader:
        assert len(await anext(reader)) > 0


async def test_conditional_writes(object_store: ObjectStore) -> None:
    first = await object_store.put("conditional", b"one", if_none_match=True)

    with pytest.raises(ObjectConflict):
        await object_store.put("conditional", b"overwrite", if_none_match=True)
    with pytest.raises(ObjectConflict):
        await object_store.put("conditional", b"overwrite", if_match="wrong-version")
    assert await _read(object_store, "conditional") == b"one"

    second = await object_store.put("conditional", b"two", if_match=first.version)
    assert second.version != first.version
    await object_store.delete("conditional")
    await object_store.delete("conditional")
    with pytest.raises(ObjectNotFound):
        await object_store.stat("conditional")


async def test_concurrent_create_only_has_one_winner(object_store: ObjectStore) -> None:
    outcomes: list[str] = []

    async def create(value: bytes) -> None:
        try:
            await object_store.put("race", value, if_none_match=True)
        except ObjectConflict:
            outcomes.append("conflict")
        else:
            outcomes.append("written")

    async with anyio.create_task_group() as tasks:
        for index in range(8):
            tasks.start_soon(create, str(index).encode())

    assert outcomes.count("written") == 1
    assert outcomes.count("conflict") == 7


async def test_list_is_ordered_prefixed_and_paginated(object_store: ObjectStore) -> None:
    for key in ("prefix/c", "other/a", "prefix/a", "prefix/b"):
        await object_store.put(key, key.encode())

    first = await object_store.list(prefix="prefix/", limit=2)
    second = await object_store.list(prefix="prefix/", cursor=first.cursor, limit=2)

    assert [item.key for item in first.items] == ["prefix/a", "prefix/b"]
    assert first.cursor is not None
    assert [item.key for item in second.items] == ["prefix/c"]
    assert second.cursor is None


@pytest.mark.parametrize("key", ["", "/absolute", "../escape", "a/../../escape", "C:\\escape", "nul\x00key"])
async def test_invalid_keys_fail_before_dispatch(object_store: ObjectStore, key: str) -> None:
    with pytest.raises(InvalidObjectRequest):
        await object_store.put(key, b"value")


async def test_invalid_range_and_cursor(object_store: ObjectStore) -> None:
    await object_store.put("object", b"value")

    with pytest.raises(InvalidObjectRequest):
        await _read(object_store, "object", byte_range=ByteRange(5))
    with pytest.raises(InvalidObjectRequest):
        await object_store.list(prefix="object", cursor="not-a-cursor")

    with pytest.raises(InvalidObjectRequest):
        ByteRange(cast(int, False))


@pytest.mark.parametrize(
    "metadata",
    [{"": "value"}, {"Key": "one", "key": "two"}, {"bad key": "value"}, {"key": "值"}, {"key": "bad\nvalue"}],
)
async def test_invalid_metadata_fails_before_dispatch(
    object_store: ObjectStore,
    metadata: dict[str, str],
) -> None:
    with pytest.raises(InvalidObjectRequest):
        await object_store.put("object", b"value", metadata=metadata)


async def test_large_stream_uses_bounded_chunks(object_store: ObjectStore) -> None:
    chunk = b"x" * (1024 * 1024)

    await object_store.put("large", _source(*(chunk for _ in range(10))))

    found = await object_store.stat("large")
    assert found.size == 10 * 1024 * 1024
    assert len(await _read(object_store, "large")) == found.size
