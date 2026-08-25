from pathlib import Path

import anyio
import pytest
from converge_foundation_service.storage.filesystem import atomic_write, prepare_root, resolve_under_root

pytestmark = pytest.mark.anyio


async def _chunks(*values: bytes):
    for value in values:
        yield value


async def test_prepare_and_resolve_confined_path(tmp_path: Path) -> None:
    root = await prepare_root(tmp_path / "files", create=True)

    path = await resolve_under_root(root, "exports/result.json")

    assert path == root / "exports" / "result.json"


@pytest.mark.parametrize(
    "logical_path",
    ["/etc/passwd", "../secret", "C:\\secret", "a/../../secret", "a//file", "a/./file"],
)
async def test_resolve_rejects_escape(tmp_path: Path, logical_path: str) -> None:
    root = await prepare_root(tmp_path / "files", create=True)

    with pytest.raises(ValueError):
        await resolve_under_root(root, logical_path)


async def test_resolve_rejects_symlink_component(tmp_path: Path) -> None:
    root = await prepare_root(tmp_path / "files", create=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        await resolve_under_root(root, "escape/value")


async def test_atomic_write_publishes_complete_file(tmp_path: Path) -> None:
    destination = tmp_path / "files" / "result.bin"

    await atomic_write(destination, _chunks(b"alpha", b"beta"))

    assert destination.read_bytes() == b"alphabeta"
    assert not list(destination.parent.glob(".*.tmp"))


async def test_atomic_write_preserves_previous_file_on_source_failure(tmp_path: Path) -> None:
    destination = tmp_path / "result.bin"
    destination.write_bytes(b"previous")

    async def failing_source():
        yield b"partial"
        raise RuntimeError("source failed")

    with pytest.raises(RuntimeError, match="source failed"):
        await atomic_write(destination, failing_source())

    assert destination.read_bytes() == b"previous"
    assert not list(tmp_path.glob(".*.tmp"))


async def test_atomic_write_removes_temporary_file_when_cancelled(tmp_path: Path) -> None:
    destination = tmp_path / "result.bin"
    destination.write_bytes(b"previous")
    started = anyio.Event()

    async def blocked_source():
        yield b"partial"
        started.set()
        await anyio.sleep_forever()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(atomic_write, destination, blocked_source())
        await started.wait()
        tasks.cancel_scope.cancel()

    assert destination.read_bytes() == b"previous"
    assert not list(tmp_path.glob(".*.tmp"))
