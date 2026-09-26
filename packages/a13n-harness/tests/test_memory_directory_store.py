from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable
from pathlib import Path

import pytest
from a13n_harness.providers.memory import (
    Changes,
    DirectoryFileStore,
    FileEntry,
    FileFormat,
    FileStore,
    FullResync,
    MemoryStoreError,
    Origin,
    SearchableFileStore,
)

pytestmark = pytest.mark.anyio

ORIGIN = Origin(run_id="run-1", principal_id="user-1", tool_call_id="call-1")


async def _failure(call: Awaitable[object]) -> MemoryStoreError:
    with pytest.raises(MemoryStoreError) as caught:
        await call
    return caught.value


def test_directory_store_is_a_file_store_without_native_search(tmp_path: Path) -> None:
    store = DirectoryFileStore(tmp_path)
    assert isinstance(store, FileStore)
    assert not isinstance(store, SearchableFileStore)


async def test_writes_are_compare_and_swap_on_content_versions(tmp_path: Path) -> None:
    store = DirectoryFileStore(tmp_path / "memory")
    first = await store.write("notes/tea.md", "likes tea\n", expected=None, origin=ORIGIN)

    assert (tmp_path / "memory/notes/tea.md").read_text() == "likes tea\n"
    assert (await store.read("notes/tea.md")).version == first
    assert await store.list() == [FileEntry(path="notes/tea.md", version=first, size=10, description="likes tea")]

    exists = await _failure(store.write("notes/tea.md", "other\n", expected=None, origin=ORIGIN))
    assert exists.code == "version_mismatch"
    assert exists.current is not None and exists.current.text == "likes tea\n"

    second = await store.write("notes/tea.md", "likes coffee\n", expected=first, origin=ORIGIN)
    assert second != first
    stale = await _failure(store.write("notes/tea.md", "late\n", expected=first, origin=ORIGIN))
    assert stale.code == "version_mismatch"
    assert stale.current is not None and stale.current.version == second
    assert (await store.read("notes/tea.md")).text == "likes coffee\n"


async def test_identical_content_has_the_same_version(tmp_path: Path) -> None:
    store = DirectoryFileStore(tmp_path)
    a = await store.write("a.md", "same\n", expected=None, origin=ORIGIN)
    b = await store.write("b.md", "same\n", expected=None, origin=ORIGIN)
    assert a == b


async def test_delete_checks_the_version_and_prunes_empty_directories(tmp_path: Path) -> None:
    store = DirectoryFileStore(tmp_path)
    version = await store.write("a/b/c.md", "x\n", expected=None, origin=ORIGIN)

    stale = await _failure(store.delete("a/b/c.md", expected="0" * 16, origin=ORIGIN))
    assert stale.code == "version_mismatch"
    assert stale.current is not None and stale.current.version == version

    await store.delete("a/b/c.md", expected=version, origin=ORIGIN)
    assert await store.list() == []
    assert not (tmp_path / "a").exists()

    missing = await _failure(store.delete("a/b/c.md", expected=version, origin=ORIGIN))
    assert missing.code == "version_mismatch"
    assert missing.current is None


async def test_move_is_one_rename_that_refuses_an_existing_destination(tmp_path: Path) -> None:
    store = DirectoryFileStore(tmp_path)
    version = await store.write("inbox/tea.md", "likes tea\n", expected=None, origin=ORIGIN)
    taken = await store.write("archive/tea.md", "old\n", expected=None, origin=ORIGIN)

    exists = await _failure(store.move("inbox/tea.md", "archive/tea.md", expected=version, origin=ORIGIN))
    assert exists.code == "already_exists"
    assert exists.current is not None and exists.current.version == taken

    stale = await _failure(store.move("inbox/tea.md", "tea.md", expected=taken, origin=ORIGIN))
    assert stale.code == "version_mismatch"

    assert await store.move("inbox/tea.md", "prefs/tea.md", expected=version, origin=ORIGIN) == version
    assert [entry.path for entry in await store.list()] == ["archive/tea.md", "prefs/tea.md"]
    assert not (tmp_path / "inbox").exists()


async def test_reads_of_missing_files_are_not_found(tmp_path: Path) -> None:
    store = DirectoryFileStore(tmp_path / "missing-root")
    assert await store.list() == []
    with pytest.raises(MemoryStoreError) as caught:
        await store.read("absent.md")
    assert caught.value.code == "not_found"


async def test_bookkeeping_and_unreadable_files_stay_out_of_the_listing(tmp_path: Path) -> None:
    store = DirectoryFileStore(tmp_path)
    await store.write("kept.md", "kept\n", expected=None, origin=ORIGIN)
    (tmp_path / "binary.bin").write_bytes(b"\xff\xfe")
    (tmp_path / "broken.md").write_text("---\ndescription: [unclosed\n---\n")

    assert (tmp_path / ".a13n-memory").is_dir()
    entries = {entry.path: entry for entry in await store.list()}
    assert set(entries) == {"broken.md", "kept.md"}
    assert entries["broken.md"].description is None

    with pytest.raises(MemoryStoreError) as caught:
        await store.write(".a13n-memory/lock", "x", expected=None, origin=ORIGIN)
    assert caught.value.code == "invalid_path"
    with pytest.raises(MemoryStoreError) as caught:
        await store.read("binary.bin")
    assert caught.value.code == "invalid_file"


async def test_symbolic_links_are_never_followed(tmp_path: Path) -> None:
    root, outside = tmp_path / "memory", tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("secret\n")
    store = DirectoryFileStore(root)
    await store.write("kept.md", "kept\n", expected=None, origin=ORIGIN)
    (root / "linked.md").symlink_to(outside / "secret.md")
    (root / "linked").symlink_to(outside)

    assert [entry.path for entry in await store.list()] == ["kept.md"]
    for call in (
        store.read("linked.md"),
        store.read("linked/secret.md"),
        store.write("linked/new.md", "x\n", expected=None, origin=ORIGIN),
        store.move("kept.md", "linked/kept.md", expected=(await store.read("kept.md")).version, origin=ORIGIN),
    ):
        assert (await _failure(call)).code == "invalid_path"
    assert sorted(path.name for path in outside.iterdir()) == ["secret.md"]


async def test_writes_apply_the_file_format(tmp_path: Path) -> None:
    store = DirectoryFileStore(tmp_path, format=FileFormat(max_file_bytes=8))
    for path, text, code in [
        ("big.md", "123456789", "too_large"),
        ("bad.md", "---\nx", "invalid_file"),
        ("../escape.md", "x", "invalid_path"),
    ]:
        with pytest.raises(MemoryStoreError) as caught:
            await store.write(path, text, expected=None, origin=ORIGIN)
        assert caught.value.code == code
    assert await store.list() == []


async def test_a_file_and_a_directory_cannot_share_a_name(tmp_path: Path) -> None:
    store = DirectoryFileStore(tmp_path)
    await store.write("notes/tea.md", "x\n", expected=None, origin=ORIGIN)
    for path in ("notes", "notes/tea.md/deeper.md"):
        with pytest.raises(MemoryStoreError) as caught:
            await store.write(path, "y\n", expected=None, origin=ORIGIN)
        assert caught.value.code == "invalid_path"
    assert list((tmp_path / ".a13n-memory").iterdir()) == [tmp_path / ".a13n-memory/lock"]


async def test_the_cursor_is_a_listing_digest_and_any_change_resyncs(tmp_path: Path) -> None:
    store = DirectoryFileStore(tmp_path)
    first = await store.changes(None)
    assert isinstance(first, FullResync)
    assert await store.changes(first.cursor) == Changes(cursor=first.cursor, paths=())

    version = await store.write("a.md", "a\n", expected=None, origin=ORIGIN)
    written = await store.changes(first.cursor)
    assert isinstance(written, FullResync)
    assert written.cursor != first.cursor

    await store.write("a.md", "b\n", expected=version, origin=ORIGIN)
    edited = await store.changes(written.cursor)
    assert isinstance(edited, FullResync)
    assert edited.cursor not in {first.cursor, written.cursor}


async def test_concurrent_tasks_linearize_their_compare_and_swap(tmp_path: Path) -> None:
    store = DirectoryFileStore(tmp_path)
    await store.write("log.md", "", expected=None, origin=ORIGIN)

    async def append(token: str) -> None:
        while True:
            current = await store.read("log.md")
            try:
                await store.write("log.md", current.text + f"{token}\n", expected=current.version, origin=ORIGIN)
                return
            except MemoryStoreError as error:
                assert error.code == "version_mismatch"

    await asyncio.gather(*(append(f"t{index}") for index in range(20)))
    assert sorted((await store.read("log.md")).text.split()) == sorted(f"t{index}" for index in range(20))


_INCREMENT = """
import asyncio, sys
from a13n_harness.providers.memory import DirectoryFileStore, MemoryStoreError, Origin

async def main(root, count):
    store = DirectoryFileStore(root)
    for _ in range(count):
        while True:
            current = await store.read("counter")
            try:
                await store.write("counter", str(int(current.text) + 1), expected=current.version, origin=Origin())
                break
            except MemoryStoreError as error:
                assert error.code == "version_mismatch"

asyncio.run(main(sys.argv[1], int(sys.argv[2])))
"""


async def test_concurrent_processes_linearize_their_compare_and_swap(tmp_path: Path) -> None:
    store = DirectoryFileStore(tmp_path)
    await store.write("counter", "0", expected=None, origin=ORIGIN)
    # Two writers of 40 increments collide about as often as four writers of 10, with half the interpreters.
    processes = [
        await asyncio.create_subprocess_exec(sys.executable, "-c", _INCREMENT, str(tmp_path), "40") for _ in range(2)
    ]
    assert [await process.wait() for process in processes] == [0, 0]
    assert (await store.read("counter")).text == "80"


async def test_purge_removes_every_file(tmp_path: Path) -> None:
    store = DirectoryFileStore(tmp_path / "memory")
    await store.write("a/b.md", "x\n", expected=None, origin=ORIGIN)
    await store.purge()
    assert not (tmp_path / "memory").exists()
    assert await store.list() == []
    await store.purge()
