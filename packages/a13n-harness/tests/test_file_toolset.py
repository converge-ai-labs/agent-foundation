"""File Toolset contracts exercised without an agent/model execution loop."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_environment.direct_local.files import LocalFileOperator
from a13n_environment.files import FileEntriesResult, FileMetadata
from a13n_harness.toolsets.files import FileTextEdit, FileToolset
from a13n_harness.toolsets.output import disclose_sequence_field

from .environment_helpers import DirectLocalFilePolicy

pytestmark = pytest.mark.anyio


@pytest.fixture
def file_tools(tmp_path: Path):
    files = LocalFileOperator(
        root=tmp_path,
        policy=DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        execution_id="file-tools",
        generation="test",
    )
    context = cast(Any, SimpleNamespace(deps=SimpleNamespace(), capabilities={}))
    return FileToolset(files), context


async def test_incomplete_oversized_sequence_spills_before_preserving_provider_cursor() -> None:
    spilled: list[bytes] = []

    class Context:
        async def _spill_tool_result(self, data: bytes, *, suffix: str) -> str:
            assert suffix == ".json"
            spilled.append(data)
            return "/workspace/page.json"

    value = {
        "ok": True,
        "entries": [{"path": f"/entry-{index}-{'x' * 80}"} for index in range(200)],
        "has_more": True,
        "next_offset": 200,
    }
    bounded, showing = await disclose_sequence_field(
        cast(Any, Context()),
        cast(Any, value),
        field="entries",
        content_complete=False,
        noun="test page",
        continuation_hint="Continue from next_offset.",
    )

    assert showing < len(value["entries"])
    assert bounded["next_offset"] == 200
    assert bounded["disclosure"]["output_file_path"] == "/workspace/page.json"
    assert b"entry-199" in spilled[0]


async def test_file_list_restarts_page_when_secondary_spill_is_unavailable() -> None:
    class PagingFiles:
        async def list(self, path: str, *, offset: int, max_results: int, include_hidden: bool):
            del path, include_hidden
            entries = tuple(
                FileMetadata(
                    path=f"/entry-{index}-{'x' * 80}",
                    kind="file",
                    size=1,
                    writable=False,
                )
                for index in range(offset, min(200, offset + max_results))
            )
            return FileEntriesResult(
                entries=entries,
                offset=offset,
                has_more=offset + len(entries) < 200,
            )

    class Context:
        async def _spill_tool_result(self, data: bytes, *, suffix: str) -> None:
            del data, suffix
            return None

    toolset = FileToolset(cast(Any, PagingFiles()))
    ctx = cast(Any, SimpleNamespace(deps=Context()))

    oversized = await toolset.ls(ctx, "/", max_results=200)
    retried = await toolset.ls(ctx, "/", offset=oversized["next_offset"], max_results=10)

    assert oversized["showing"] < 200
    assert oversized["next_offset"] == 0
    assert oversized["has_more"] is True
    assert oversized["disclosure"]["output_file_path"] is None
    assert "smaller max_results" in oversized["disclosure"]["hint"]
    assert retried["entries"] == [
        {"path": f"/entry-{index}-{'x' * 80}", "kind": "file", "size": 1, "writable": False} for index in range(10)
    ]
    assert retried["next_offset"] == 10


@pytest.mark.parametrize("provider_budget", [9, 1000])
@pytest.mark.parametrize("line", ["abcd\n", "中文\n", "𐐀\r\n"])
async def test_text_view_batches_obey_actual_page_budget(tmp_path: Path, provider_budget: int, line: str) -> None:
    content = line * 7 + "tail"
    (tmp_path / "text").write_text(content, encoding="utf-8", newline="")
    files = LocalFileOperator(
        root=tmp_path,
        policy=DirectLocalFilePolicy(max_value_bytes=provider_budget),
        execution_id="workspace",
        generation="test",
    )
    toolset = FileToolset(files)
    pages = []
    offset = 0
    for _ in range(10):
        page = await toolset._read_text_page(
            files, "/text", line_offset=offset, line_limit=1000, max_line_length=20_000, page_bytes=19
        )
        assert len(page.text.encode("utf-8")) <= 19
        assert page.lines_read == len(page.text.splitlines()) > 0
        assert not page.truncated_lines
        pages.append(page.text)
        offset += page.lines_read
        if not page.has_more:
            break
    else:
        pytest.fail("Text pagination made no bounded progress")
    assert "".join(pages) == content
    assert offset == 8


async def test_file_toolset_creates_nested_parents_and_returns_stable_missing_error(tmp_path: Path) -> None:
    files = LocalFileOperator(
        root=tmp_path,
        policy=DirectLocalFilePolicy(max_value_bytes=16 * 1024 * 1024),
        execution_id="mount-1",
        generation="generation-1",
    )
    toolset = FileToolset(files)
    ctx = cast(Any, SimpleNamespace(deps=SimpleNamespace(environment=SimpleNamespace(files=files)), capabilities={}))

    written = await toolset.write(ctx, "/one/two/value.txt", "written")
    created = await toolset.edit(ctx, "/three/four/value.txt", "", "created")
    missing = await toolset.view(ctx, "/missing/ancestor/value.txt")

    assert written["ok"] is True
    assert created["ok"] is True
    assert (tmp_path / "one" / "two" / "value.txt").read_text() == "written"
    assert (tmp_path / "three" / "four" / "value.txt").read_text() == "created"
    assert missing["ok"] is False
    assert missing["error"]["code"] == "environment_not_found"


async def test_exact_edits_are_agent_friendly_and_failed_batch_is_not_published(tmp_path: Path, file_tools) -> None:
    target = tmp_path / "edit.txt"
    target.write_text("alpha\nbeta\nbeta\n")
    toolset, ctx = file_tools
    failed = await toolset.multi_edit(
        ctx,
        "/edit.txt",
        [
            FileTextEdit(old_string="alpha", new_string="changed"),
            FileTextEdit(old_string="missing", new_string="never-written"),
        ],
    )
    assert target.read_text() == "alpha\nbeta\nbeta\n"
    changed = await toolset.edit(ctx, "/edit.txt", "beta", "gamma", replace_all=True)
    observed = [failed, changed]
    assert observed[0]["error"]["code"] == "environment_edit_not_found"
    assert observed[0]["error"]["details"] == {
        "edit_index": 2,
        "hint": "Read the current target and copy an exact old_string, including whitespace, before retrying the edit.",
    }
    assert observed[1]["ok"] is True
    assert target.read_text() == "alpha\ngamma\ngamma\n"


@pytest.mark.parametrize("file_root", [False, True])
async def test_grep_returns_requested_context_at_file_boundaries(tmp_path: Path, file_root: bool, file_tools) -> None:
    (tmp_path / "context.txt").write_bytes(
        b"needle0 top\nbefore middle\nneedle1 middle\nafter middle\nneedle2 bottom\n"
    )
    toolset, ctx = file_tools
    requests = (
        {"pattern": "needle0", "context_lines": 0},
        {"pattern": "needle1", "context_lines": 1},
        {"pattern": "needle2", "context_lines": 2},
    )
    if file_root:
        requests = tuple({**request, "root": "/context.txt"} for request in requests)

    observed = [await toolset.grep(ctx, **{"root": "/", **request}) for request in requests]
    assert all(item["ok"] for item in observed), observed
    matches = {match["matching_line"]: match for item in observed for match in item["matches"].values()}
    assert matches["needle0 top"]["context_start_line"] == 1
    assert matches["needle0 top"]["context"].splitlines() == ["needle0 top"]
    assert matches["needle1 middle"]["context_start_line"] == 2
    assert matches["needle1 middle"]["context"].splitlines() == [
        "before middle",
        "needle1 middle",
        "after middle",
    ]
    assert matches["needle2 bottom"]["context_start_line"] == 3
    assert matches["needle2 bottom"]["context"].splitlines() == [
        "needle1 middle",
        "after middle",
        "needle2 bottom",
    ]


@pytest.mark.parametrize("line_count", [160, 520])
@pytest.mark.parametrize("line_text", ["x" * 90, "中文𐐀" * 30, '\\"' * 45])
async def test_text_view_output_budget_continues_without_skipping_lines(
    tmp_path: Path, line_count: int, line_text: str, file_tools
) -> None:
    content = "".join(f"line {index}: {line_text}\n" for index in range(line_count)) + "tail"
    (tmp_path / "notes.md").write_bytes(content.encode("utf-8"))
    pages: list[dict[str, Any]] = []

    toolset, ctx = file_tools
    offset = 0
    for _ in range(line_count + 1):
        page = await toolset.view(ctx, "/notes.md", line_offset=offset, line_limit=1000)
        assert isinstance(page, dict)
        pages.append(page)
        assert page["ok"] is True
        assert page["file_path"] == "/notes.md"
        assert page["truncated_lines"] == []
        assert page["lines_read"] == len(page["content"].splitlines())
        assert content.startswith("".join(item["content"] for item in pages))
        assert len(json.dumps(page, ensure_ascii=False, separators=(",", ":"))) <= 12_000
        if not page["has_more"]:
            assert "".join(item["content"] for item in pages) == content
            assert "next_line_offset" not in page
            break
        offset = page["next_line_offset"]
        assert offset == page["line_offset"] + page["lines_read"]
        assert offset > page["line_offset"]
        assert page["content"].endswith("\n")
        assert page["disclosure"]["content_complete"] is False
        assert page["disclosure"]["output_file_path"] is None
        assert "next_line_offset" in page["disclosure"]["hint"]
    else:
        pytest.fail("Text pagination made no bounded progress")
    assert len(pages) >= 2


async def test_large_environment_result_is_bounded_without_retry_shaped_failure(tmp_path: Path, file_tools) -> None:
    (tmp_path / "large.txt").write_text("\\" * (256 * 1024))
    toolset, ctx = file_tools
    observed = await toolset.view(ctx, "/large.txt", line_limit=1)
    assert observed["ok"] is True
    assert observed["has_more"] is False
    assert observed["truncated_lines"] == [1]
    assert observed["disclosure"]["content_complete"] is False
    assert "one-based" in observed["disclosure"]["hint"]
    assert "max_line_length" in observed["disclosure"]["hint"]
    assert "next_line_offset" not in observed
    assert isinstance(observed["content"], str)
    assert len(observed["content"]) == 2_000
