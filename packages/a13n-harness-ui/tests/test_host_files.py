from __future__ import annotations

import os
from pathlib import Path
from threading import Event as ThreadEvent

import pytest
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.host_files import (
    MAX_TEXT_BYTES,
    DirectoryCreateRequest,
    FileCaptureRequest,
    FileDeleteRequest,
    FileMoveRequest,
    FileReadRequest,
    FileWriteRequest,
    HostFiles,
)
from a13n_harness_ui.thread_files import MAX_ATTACHMENT_BYTES
from anyio import CancelScope, create_task_group, sleep, to_thread

pytestmark = pytest.mark.anyio


async def test_disabled_gate_precedes_filesystem_access(tmp_path: Path) -> None:
    files = HostFiles()
    with pytest.raises(HarnessUiError) as failure:
        await files.metadata(str(tmp_path / "missing"))
    assert failure.value.code == "host_files_disabled"
    with pytest.raises(HarnessUiError, match="share-computer"):
        await files.write(str(tmp_path / "new"), b"never written", expected_revision=None)
    assert not (tmp_path / "new").exists()


async def test_create_browse_edit_conflict_move_delete(tmp_path: Path) -> None:
    files = HostFiles(enabled=True)
    root = tmp_path / "not-a-repository"
    await files.create_directory(DirectoryCreateRequest(path=str(root)))
    paths = [root / "a.txt", root / "b.txt", root / "c.txt"]
    for path in paths:
        await files.write_text(FileWriteRequest(path=str(path), text="first\r\nsecond\n"))
    first = await files.browse(str(root), limit=2)
    assert first.next_offset == 2
    second = await files.browse(str(root), limit=2, offset=2, revision=first.directory.revision)
    assert second.next_offset is None
    assert [Path(item.path).name for item in first.entries + second.entries] == ["a.txt", "b.txt", "c.txt"]
    with pytest.raises(HarnessUiError, match="revision"):
        await files.browse(str(root), offset=2)
    text = await files.read_text(FileReadRequest(path=str(paths[0])))
    assert text.text == "first\r\nsecond\n"
    paths[0].write_text("external edit")
    with pytest.raises(HarnessUiError) as failure:
        await files.write_text(
            FileWriteRequest(path=str(paths[0]), text="stale", expected_revision=text.entry.revision)
        )
    assert failure.value.code == "host_files_conflict"
    assert paths[0].read_text() == "external edit"
    current = await files.metadata(str(paths[0]))
    paths[0].chmod(0o640)
    current = await files.metadata(str(paths[0]))
    saved = await files.write_text(
        FileWriteRequest(path=str(paths[0]), text="saved", expected_revision=current.revision)
    )
    if os.name == "posix":
        assert paths[0].stat().st_mode & 0o777 == 0o640
    with pytest.raises(HarnessUiError, match="exists"):
        await files.move(
            FileMoveRequest(path=str(paths[0]), destination=str(paths[1]), expected_revision=saved.revision)
        )
    other_root = tmp_path / "other-root"
    other_root.mkdir()
    destination = other_root / "renamed.txt"
    moved = await files.move(
        FileMoveRequest(path=str(paths[0]), destination=str(destination), expected_revision=saved.revision)
    )
    assert moved.path == str(destination)
    assert destination.read_text() == "saved"
    assert not paths[0].exists()
    removed = await files.delete(FileDeleteRequest(path=str(destination), expected_revision=moved.revision))
    assert removed.removed_entries == 1
    with pytest.raises(HarnessUiError) as failure:
        await files.browse(str(root), offset=2, revision=first.directory.revision)
    assert failure.value.code == "host_files_conflict"


async def test_symlinks_are_visible_read_targets_explicit_and_delete_does_not_follow(tmp_path: Path) -> None:
    files = HostFiles(enabled=True)
    target = tmp_path / "target.txt"
    target.write_text("target")
    link = tmp_path / "link"
    link.symlink_to(target)
    entry = await files.metadata(str(link))
    assert entry.kind == "symlink" and entry.link_target is not None
    assert Path(entry.link_target).resolve() == target.resolve()
    text = await files.read_text(FileReadRequest(path=str(link)))
    assert text.resolved_path == str(target) and text.text == "target"
    with pytest.raises(HarnessUiError):
        await files.write_text(FileWriteRequest(path=str(link), expected_revision=entry.revision, text="no"))
    await files.delete(FileDeleteRequest(path=str(link), expected_revision=entry.revision, recursive=True))
    assert target.read_text() == "target"
    directory = tmp_path / "tree"
    directory.mkdir()
    (directory / "outside").symlink_to(tmp_path, target_is_directory=True)
    (directory / "sub").mkdir()
    (directory / "sub" / "inner").write_bytes(b"inner")
    observed = await files.metadata(str(directory))
    with pytest.raises(HarnessUiError):
        await files.delete(FileDeleteRequest(path=str(directory), expected_revision=observed.revision))
    result = await files.delete(
        FileDeleteRequest(path=str(directory), expected_revision=observed.revision, recursive=True)
    )
    assert result.removed_entries == 4
    assert target.exists() and not directory.exists()
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "missing")
    assert (await files.metadata(str(dangling))).kind == "symlink"
    with pytest.raises(HarnessUiError) as failure:
        await files.download(FileReadRequest(path=str(dangling)))
    assert failure.value.code == "host_files_not_found"


async def test_binary_large_special_and_path_limits(tmp_path: Path) -> None:
    files = HostFiles(enabled=True)
    binary = tmp_path / "binary"
    await files.write(str(binary), b"\x00\xff", expected_revision=None)
    assert (await files.read_text(FileReadRequest(path=str(binary)))).presentation == "binary"
    assert (await files.download(FileReadRequest(path=str(binary)))).data == b"\x00\xff"
    large = tmp_path / "large"
    large.write_bytes(b"a" * (MAX_TEXT_BYTES + 1))
    text = await files.read_text(FileReadRequest(path=str(large)))
    assert text.presentation == "too_large" and text.text is None
    assert len((await files.download(FileReadRequest(path=str(large)))).data) == MAX_TEXT_BYTES + 1
    with large.open("wb") as stream:
        stream.truncate(MAX_ATTACHMENT_BYTES + 1)
    with pytest.raises(HarnessUiError) as failure:
        await files.download(FileReadRequest(path=str(large)))
    assert failure.value.code == "host_files_too_large"
    with pytest.raises(HarnessUiError):
        await files.write(str(tmp_path / "oversize"), b"x" * (MAX_ATTACHMENT_BYTES + 1), expected_revision=None)
    assert not (tmp_path / "oversize").exists()
    for invalid in ("relative", "\x00", "x" * 4097):
        with pytest.raises(HarnessUiError) as failure:
            await files.metadata(invalid)
        assert failure.value.code == "host_files_path_invalid"
    if os.name == "posix":
        fifo = tmp_path / "fifo"
        os.mkfifo(fifo)
        assert (await files.metadata(str(fifo))).kind == "other"
        with pytest.raises(HarnessUiError, match="regular files"):
            await files.download(FileReadRequest(path=str(fifo)))
    with pytest.raises(ValueError):
        FileWriteRequest(path=str(binary), text="\ud800")


async def test_capture_requires_reviewed_revision_and_keeps_exact_lines(tmp_path: Path) -> None:
    files = HostFiles(enabled=True)
    path = tmp_path / "a.py"
    path.write_bytes(b"one\r\ntwo\r\nthree")
    revision = (await files.read_text(FileReadRequest(path=str(path)))).entry.revision
    capture = await files.capture(
        FileCaptureRequest(path=str(path), expected_revision=revision, start_line=2, end_line=2)
    )
    assert capture.data == b"two\r\n"
    assert capture.source.start_line == capture.source.end_line == 2
    assert capture.source.location == "host"
    with pytest.raises(HarnessUiError, match="exceed"):
        await files.capture(FileCaptureRequest(path=str(path), expected_revision=revision, start_line=1, end_line=4))
    path.write_text("edited after selection")
    assert capture.data == b"two\r\n"
    with pytest.raises(HarnessUiError) as failure:
        await files.capture(FileCaptureRequest(path=str(path), expected_revision=revision))
    assert failure.value.code == "host_files_conflict"


async def test_write_rechecks_external_edit_and_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = HostFiles(enabled=True)
    path = tmp_path / "file"
    path.write_bytes(b"original")
    revision = (await files.metadata(str(path))).revision
    real_fsync = os.fsync

    def external_edit(fd: int) -> None:
        path.write_bytes(b"external")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", external_edit)
    with pytest.raises(HarnessUiError) as failure:
        await files.write(str(path), b"stale", expected_revision=revision)
    assert failure.value.code == "host_files_conflict"
    assert path.read_bytes() == b"external"
    assert not list(tmp_path.glob(".a13n-write-*"))


async def test_permission_error_and_failed_write_leave_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = HostFiles(enabled=True)
    path = tmp_path / "file"
    path.write_bytes(b"original")
    revision = (await files.metadata(str(path))).revision

    def denied(*args, **kwargs):
        raise PermissionError("sensitive internal details")

    monkeypatch.setattr(os, "replace", denied)
    with pytest.raises(HarnessUiError) as failure:
        await files.write(str(path), b"new", expected_revision=revision)
    assert failure.value.code == "host_files_permission_denied"
    assert "sensitive" not in str(failure.value)
    assert path.read_bytes() == b"original"
    assert not list(tmp_path.glob(".a13n-write-*"))


async def test_delete_partial_failure_and_preflight_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness_ui.host_files as module

    files = HostFiles(enabled=True)
    tree = tmp_path / "tree"
    tree.mkdir()
    for name in ("a", "b", "c"):
        (tree / name).write_text(name)
    revision = (await files.metadata(str(tree))).revision
    monkeypatch.setattr(module, "MAX_DELETE_ENTRIES", 2)
    with pytest.raises(HarnessUiError) as failure:
        await files.delete(FileDeleteRequest(path=str(tree), expected_revision=revision, recursive=True))
    assert failure.value.code == "host_files_too_large"
    assert len(list(tree.iterdir())) == 3
    monkeypatch.setattr(module, "MAX_DELETE_ENTRIES", 10000)
    real_unlink = os.unlink
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("denied")
        return real_unlink(*args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_second)
    with pytest.raises(HarnessUiError) as failure:
        await files.delete(FileDeleteRequest(path=str(tree), expected_revision=revision, recursive=True))
    assert failure.value.code == "host_files_partial_failure"
    assert "1 entries" in str(failure.value)
    assert len(list(tree.iterdir())) == 2


async def test_started_write_is_not_abandoned_on_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = HostFiles(enabled=True)
    path = tmp_path / "file"
    entered, release = ThreadEvent(), ThreadEvent()
    real_fsync = os.fsync

    def slow_fsync(fd: int) -> None:
        entered.set()
        assert release.wait(5)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", slow_fsync)
    returned = []
    scope = CancelScope()

    async def write() -> None:
        with scope:
            returned.append(await files.write(str(path), b"complete", expected_revision=None))

    async with create_task_group() as tasks:
        tasks.start_soon(write)
        assert await to_thread.run_sync(entered.wait, 5)
        scope.cancel()
        await sleep(0)
        assert not path.exists()
        release.set()
    assert path.read_bytes() == b"complete"
    assert len(returned) == 1
    # A second operation sees the completed mutation, not an abandoned background writer.
    assert (await files.metadata(str(path))).revision == returned[0].revision


async def test_move_never_overwrites_concurrent_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import a13n_harness_ui.host_files as module

    files = HostFiles(enabled=True)
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.write_bytes(b"source")
    original = module._rename_no_replace

    def race(src: Path, dst: Path) -> None:
        dst.write_bytes(b"external")
        original(src, dst)

    monkeypatch.setattr(module, "_rename_no_replace", race)
    revision = (await files.metadata(str(source))).revision
    with pytest.raises(HarnessUiError) as failure:
        await files.move(FileMoveRequest(path=str(source), destination=str(destination), expected_revision=revision))
    assert failure.value.code == "host_files_conflict"
    assert source.read_bytes() == b"source" and destination.read_bytes() == b"external"


@pytest.mark.parametrize("name", ["plain", "file.txt", "script.cmd", "program.exe"])
async def test_saved_files_have_matching_path_and_handle_revisions(tmp_path: Path, name: str) -> None:
    files = HostFiles(enabled=True)
    path = tmp_path / name
    saved = await files.write(str(path), b"created", expected_revision=None)
    # Creation uses a temporary hard link; removing it changes native metadata.
    snapshot = await files.download(FileReadRequest(path=str(path), expected_revision=saved.revision))
    assert snapshot.data == b"created" and snapshot.entry.revision == saved.revision
    saved = await files.write(str(path), b"replaced", expected_revision=saved.revision)
    assert (await files.read_text(FileReadRequest(path=str(path), expected_revision=saved.revision))).text == "replaced"


async def test_atomic_save_replaces_only_selected_hardlink(tmp_path: Path) -> None:
    files = HostFiles(enabled=True)
    selected, alias = tmp_path / "selected", tmp_path / "alias"
    selected.write_bytes(b"original")
    os.link(selected, alias)
    observed = await files.metadata(str(selected))
    alias_observed = await files.metadata(str(alias))
    saved = await files.write(str(selected), b"edited", expected_revision=observed.revision)
    assert selected.read_bytes() == b"edited" and alias.read_bytes() == b"original"
    assert not os.path.samefile(selected, alias)
    assert selected.stat().st_nlink == alias.stat().st_nlink == 1
    assert (
        await files.download(FileReadRequest(path=str(selected), expected_revision=saved.revision))
    ).data == b"edited"
    with pytest.raises(HarnessUiError) as failure:
        await files.write(str(alias), b"stale", expected_revision=alias_observed.revision)
    assert failure.value.code == "host_files_conflict"
    assert alias.read_bytes() == b"original"


async def test_recursive_delete_tracks_its_own_hardlink_changes(tmp_path: Path) -> None:
    files = HostFiles(enabled=True)
    tree = tmp_path / "tree"
    tree.mkdir()
    first = tree / "a"
    first.write_bytes(b"same inode")
    os.link(first, tree / "b")
    (tree / "sub").mkdir()
    os.link(first, tree / "sub/c")
    outside = tmp_path / "outside"
    os.link(first, outside)
    revision = (await files.metadata(str(tree))).revision
    deleted = await files.delete(FileDeleteRequest(path=str(tree), expected_revision=revision, recursive=True))
    assert deleted.removed_entries == 5
    assert not tree.exists() and outside.read_bytes() == b"same inode"
    assert outside.stat().st_nlink == 1


async def test_same_app_writers_conflict_and_reads_detect_external_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = HostFiles(enabled=True)
    path = tmp_path / "shared"
    path.write_bytes(b"old")
    revision = (await files.metadata(str(path))).revision
    outcomes = []

    async def save(data: bytes) -> None:
        try:
            outcomes.append(await files.write(str(path), data, expected_revision=revision))
        except HarnessUiError as exc:
            outcomes.append(exc.code)

    async with create_task_group() as tasks:
        tasks.start_soon(save, b"one")
        tasks.start_soon(save, b"two")
    assert len(outcomes) == 2 and outcomes.count("host_files_conflict") == 1
    real_fstat = os.fstat
    calls = 0

    def concurrent_change(fd: int):
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(b"external read race")
        return real_fstat(fd)

    monkeypatch.setattr(os, "fstat", concurrent_change)
    with pytest.raises(HarnessUiError) as failure:
        await files.download(FileReadRequest(path=str(path)))
    assert failure.value.code == "host_files_conflict"


async def test_directory_bound_cross_device_and_unsupported_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno

    import a13n_harness_ui.host_files as module

    files = HostFiles(enabled=True)
    directory = tmp_path / "directory"
    directory.mkdir()
    for name in ("a", "b", "c"):
        (directory / name).write_bytes(b"x")
    monkeypatch.setattr(module, "MAX_DIRECTORY_ENTRIES", 2)
    with pytest.raises(HarnessUiError) as failure:
        await files.browse(str(directory))
    assert failure.value.code == "host_files_too_large"
    source = directory / "a"
    revision = (await files.metadata(str(source))).revision

    def cross_device(*args):
        raise OSError(errno.EXDEV, "cross device")

    monkeypatch.setattr(module, "_rename_no_replace", cross_device)
    with pytest.raises(HarnessUiError) as failure:
        await files.move(
            FileMoveRequest(path=str(source), destination=str(tmp_path / "elsewhere"), expected_revision=revision)
        )
    assert failure.value.code == "host_files_cross_device"
    assert source.exists()


async def test_recursive_delete_rejects_replaced_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = HostFiles(enabled=True)
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a").write_bytes(b"a")
    (tree / "b").write_bytes(b"b")
    revision = (await files.metadata(str(tree))).revision
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a").write_bytes(b"keep a")
    (outside / "b").write_bytes(b"keep b")
    real_unlink = os.unlink
    replaced = False

    def replace_parent(*args, **kwargs):
        nonlocal replaced
        result = real_unlink(*args, **kwargs)
        if not replaced:
            replaced = True
            tree.rename(tmp_path / "moved-away")
            tree.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(os, "unlink", replace_parent)
    with pytest.raises(HarnessUiError) as failure:
        await files.delete(FileDeleteRequest(path=str(tree), expected_revision=revision, recursive=True))
    assert failure.value.code == "host_files_partial_failure"
    assert (outside / "a").read_bytes() == b"keep a"
    assert (outside / "b").read_bytes() == b"keep b"
