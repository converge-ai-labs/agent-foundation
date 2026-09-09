"""Bounded text pages are successful observations, not oversized requests."""

from pathlib import Path

import pytest
from a13n_environment.direct_local.files import LocalFileOperator
from a13n_environment.direct_local.provider import _DirectLocalFilePolicy
from a13n_environment.models import EnvironmentError

pytestmark = pytest.mark.anyio


def _files(root: Path, budget: int) -> LocalFileOperator:
    return LocalFileOperator(
        root=root,
        read_only=True,
        policy=_DirectLocalFilePolicy(max_value_bytes=budget),
        mount_id="workspace",
        generation="test",
    )


async def test_small_file_accepts_large_requested_page(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_bytes(b"# Skill\nRead the guide.\n")
    result = await _files(tmp_path, 64).read_text("/SKILL.md", line_limit=1000, max_line_length=20_000)
    assert result.text == "# Skill\nRead the guide.\n"
    assert result.lines_read == 2
    assert result.has_more is False
    assert result.truncated_lines == ()


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
