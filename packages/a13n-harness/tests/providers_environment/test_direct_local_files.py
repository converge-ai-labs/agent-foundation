"""Bounded text pages are successful observations, not oversized requests."""

from pathlib import Path

import pytest
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
from a13n_harness.providers.environment.direct_local.provider import _DirectLocalFilePolicy
from a13n_harness.providers.environment.models import EnvironmentError

pytestmark = pytest.mark.anyio


def _files(root: Path, budget: int = 1024) -> LocalFileOperator:
    return LocalFileOperator(
        root=root,
        policy=_DirectLocalFilePolicy(max_value_bytes=budget),
        mount_id="workspace",
        generation="test",
    )


@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize("replace", [False, True])
async def test_copy_replacement_permission_does_not_require_existing_destination(tmp_path, exists, replace):
    payload = b"complete replacement"
    (tmp_path / "source").write_bytes(payload)
    if exists:
        (tmp_path / "destination").write_bytes(b"original")
    files = _files(tmp_path)
    try:
        if exists and not replace:
            with pytest.raises(EnvironmentError) as error:
                await files.copy("/source", "/destination", replace=replace)
            assert error.value.code == "environment_conflict"
            assert (tmp_path / "destination").read_bytes() == b"original"
        else:
            result = await files.copy("/source", "/destination", replace=replace)
            assert result.bytes_copied == len(payload)
            assert (tmp_path / "destination").read_bytes() == payload
        assert (tmp_path / "source").read_bytes() == payload
        assert sorted(path.name for path in tmp_path.iterdir()) == ["destination", "source"]
    finally:
        files.close()


async def test_small_file_accepts_large_requested_page(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_bytes(b"# Skill\nRead the guide.\n")
    result = await _files(tmp_path, 64).read_text("/SKILL.md", line_limit=1000, max_line_length=20_000)
    assert result.text == "# Skill\nRead the guide.\n"
    assert result.lines_read == 2
    assert result.has_more is False
    assert result.truncated_lines == ()


@pytest.mark.parametrize("destination_kind", ["empty-directory", "nonempty-directory", "file"])
async def test_replacement_move_preserves_incompatible_entries(tmp_path: Path, destination_kind: str) -> None:
    source, destination = tmp_path / "source", tmp_path / "destination"
    if destination_kind == "file":
        source.mkdir()
        (source / "source-child").write_bytes(b"source")
        destination.write_bytes(b"destination")
    else:
        source.write_bytes(b"source")
        destination.mkdir()
        if destination_kind == "nonempty-directory":
            (destination / "destination-child").write_bytes(b"destination")
    before = {
        str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None for path in tmp_path.rglob("*")
    }
    with pytest.raises(EnvironmentError) as error:
        await _files(tmp_path).move("/source", "/destination", replace=True)
    assert error.value.code == "environment_request_invalid"
    assert {
        str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None for path in tmp_path.rglob("*")
    } == before


async def test_replacement_move_publishes_complete_file_without_backup(tmp_path: Path) -> None:
    (tmp_path / "source").write_bytes(b"new")
    (tmp_path / "destination").write_bytes(b"old")
    await _files(tmp_path).move("/source", "/destination", replace=True)
    assert list(tmp_path.iterdir()) == [tmp_path / "destination"]
    assert (tmp_path / "destination").read_bytes() == b"new"


async def test_remove_missing_file_reports_absence(tmp_path: Path) -> None:
    with pytest.raises(EnvironmentError) as error:
        await _files(tmp_path).remove("/missing")
    assert error.value.code == "environment_not_found"
    assert not list(tmp_path.iterdir())


async def test_ensuring_existing_root_allows_child_writes_without_mutating_root(tmp_path: Path) -> None:
    files = _files(tmp_path, 64)
    root_identity = tmp_path.stat().st_ino
    await files.mkdir("/", parents=True, exist_ok=True)
    await files.write_text("/download.txt", "downloaded", mode="create")
    assert (tmp_path / "download.txt").read_bytes() == b"downloaded"
    assert tmp_path.stat().st_ino == root_identity
    with pytest.raises(EnvironmentError, match="root cannot be mutated"):
        await files.mkdir("/", exist_ok=False)
    (tmp_path / "download.txt").unlink()
    tmp_path.rmdir()
    with pytest.raises(EnvironmentError):
        await files.mkdir("/", parents=True, exist_ok=True)
    assert not tmp_path.exists()


@pytest.mark.parametrize("line", ["abcd\n", "中文\r\n", "𐐀𐐁\n"])
async def test_byte_budget_pages_complete_lines_without_skipping(tmp_path: Path, line: str) -> None:
    content = line * 7 + "tail"
    (tmp_path / "text").write_bytes(content.encode())
    budget = len(line.encode()) * 2
    files = _files(tmp_path, budget)
    offset = 0
    pages = []
    while True:
        page = await files.read_text("/text", line_offset=offset, line_limit=1000, max_line_length=20_000)
        assert page.lines_read > 0
        assert len(page.text.encode()) <= budget
        assert not page.truncated_lines
        pages.append(page.text)
        offset += page.lines_read
        if not page.has_more:
            break
    assert "".join(pages) == content
    assert offset == 8
    end = await files.read_text("/text", line_offset=offset)
    assert (end.text, end.lines_read, end.has_more) == ("", 0, False)


@pytest.mark.parametrize("terminator", ["", "\n"])
async def test_single_line_byte_clipping_is_utf8_safe_and_explicit(tmp_path: Path, terminator: str) -> None:
    (tmp_path / "text").write_bytes(("中文𐐀" * 10 + terminator).encode())
    page = await _files(tmp_path, 8).read_text("/text", line_limit=1000, max_line_length=20_000)
    assert page.text == "中文" + terminator
    assert page.lines_read == 1
    assert page.has_more is False
    assert page.truncated_lines == (1,)
    assert len(page.text.encode()) <= 8


async def test_clipped_line_preserves_source_number_and_later_lines(tmp_path: Path) -> None:
    (tmp_path / "text").write_bytes(b"skip\n" + b"x" * 100 + b"\nnext\n")
    files = _files(tmp_path, 8)
    page = await files.read_text("/text", line_offset=1, line_limit=1000, max_line_length=20_000)
    assert (page.text, page.lines_read, page.has_more, page.truncated_lines) == ("xxxxxxx\n", 1, True, (2,))
    next_page = await files.read_text("/text", line_offset=page.line_offset + page.lines_read)
    assert (next_page.text, next_page.has_more) == ("next\n", False)


@pytest.mark.parametrize("data", [b"x" * 100 + b"\xff\n", b"x" * 100 + b"\0\n"])
async def test_clipping_does_not_hide_invalid_source_text(tmp_path: Path, data: bytes) -> None:
    (tmp_path / "text").write_bytes(data)
    with pytest.raises(EnvironmentError) as error:
        await _files(tmp_path, 8).read_text("/text")
    assert error.value.code == "environment_unsupported"


async def test_large_bounds_do_not_mask_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(EnvironmentError) as error:
        await _files(tmp_path, 8).read_text("/missing", line_limit=1000, max_line_length=20_000)
    assert error.value.code == "environment_not_found"


async def test_default_value_budget_accepts_larger_files_but_rejects_actual_overflow(tmp_path: Path) -> None:
    configuration = DirectLocalEnvironmentConfiguration(root=DirectLocalRootConfiguration(path=tmp_path))
    assert configuration.max_value_bytes == 64 * 1024 * 1024
    files = _files(tmp_path, configuration.max_value_bytes)
    path = tmp_path / "binary"
    with path.open("wb") as stream:
        stream.truncate(17 * 1024 * 1024)
    assert len(await files.read_bytes("/binary")) == 17 * 1024 * 1024
    with path.open("wb") as stream:
        stream.truncate(configuration.max_value_bytes + 1)
    with pytest.raises(EnvironmentError) as error:
        await files.read_bytes("/binary")
    assert error.value.code == "environment_too_large"
    assert await files.read_bytes("/binary", length=8) == b"\0" * 8


@pytest.mark.parametrize("action", ["read", "stat", "write"])
async def test_closed_file_facet_rejects_native_access(tmp_path: Path, action: str) -> None:
    (tmp_path / "file").write_bytes(b"original")
    files = _files(tmp_path)
    files.close()
    with pytest.raises(EnvironmentError) as error:
        if action == "read":
            await files.read_bytes("/file")
        elif action == "stat":
            await files.stat("/file")
        else:
            await files.write_text("/file", "replacement", mode="replace")
    assert error.value.code == "environment_stale_mount"
    assert (tmp_path / "file").read_bytes() == b"original"
