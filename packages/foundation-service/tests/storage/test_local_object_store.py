from pathlib import Path

import anyio
import pytest
from a13n_service.storage.object_store import (
    LocalObjectStore,
    ObjectAccessDenied,
    ObjectConflict,
    ObjectNotFound,
    ObjectStoreUnavailable,
)

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("operation", ["open", "stat"])
async def test_permission_failure_is_not_a_transient_outage(tmp_path: Path, monkeypatch, operation: str) -> None:
    from a13n_service.storage.object_store import local

    store = await LocalObjectStore.create(tmp_path / "objects")
    await store.put("private", b"data")

    def denied(*args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr(local.os, "open", denied)
    with pytest.raises(ObjectAccessDenied):
        if operation == "stat":
            await store.stat("private")
        else:
            async with store.open("private"):
                raise AssertionError("Denied object must not be opened")


async def test_failed_stream_is_not_visible_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    store = await LocalObjectStore.create(tmp_path / "objects")

    async def failing_source():
        yield b"partial"
        raise RuntimeError("source failed")

    with pytest.raises(RuntimeError, match="source failed"):
        await store.put("failed", failing_source())

    assert list((tmp_path / "objects" / "tmp").iterdir()) == []
    assert (await store.list()).items == ()


async def test_cancelled_stream_is_not_visible_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    store = await LocalObjectStore.create(root)
    started = anyio.Event()

    async def blocked_source():
        yield b"partial"
        started.set()
        await anyio.sleep_forever()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(store.put, "cancelled", blocked_source())
        await started.wait()
        tasks.cancel_scope.cancel()

    assert list((root / "tmp").iterdir()) == []
    with pytest.raises(ObjectNotFound):
        await store.stat("cancelled")


async def test_keys_are_not_exposed_as_paths(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    store = await LocalObjectStore.create(root)

    await store.put("customer/private.txt", b"private")

    assert not (root / "customer" / "private.txt").exists()
    assert len(list((root / "objects-v1").glob("*/*.object"))) == 1


async def test_conditional_delete_is_atomic(tmp_path: Path) -> None:
    store = await LocalObjectStore.create(tmp_path / "objects")
    first = await store.put("conditional", b"one")
    second = await store.put("conditional", b"two", if_match=first.version)

    with pytest.raises(ObjectConflict):
        await store.delete("conditional", if_match=first.version)
    await store.delete("conditional", if_match=second.version)

    with pytest.raises(ObjectNotFound):
        await store.stat("conditional")


async def test_layout_rejects_symlinked_internal_directory(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "objects-v1").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ObjectStoreUnavailable, match="not a directory"):
        await LocalObjectStore.create(root)


async def test_truncated_body_is_reported_as_unavailable(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    store = await LocalObjectStore.create(root)
    await store.put("object", b"complete")
    envelope = next((root / "objects-v1").glob("*/*.object"))
    envelope.write_bytes(envelope.read_bytes()[:-1])

    with pytest.raises(ObjectStoreUnavailable, match="body size"):
        await store.stat("object")


async def test_object_file_symlink_is_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    store = await LocalObjectStore.create(root)
    await store.put("object", b"complete")
    envelope = next((root / "objects-v1").glob("*/*.object"))
    outside = tmp_path / "outside.object"
    outside.write_bytes(envelope.read_bytes())
    envelope.unlink()
    envelope.symlink_to(outside)

    with pytest.raises(ObjectStoreUnavailable, match="could not be opened"):
        await store.stat("object")
