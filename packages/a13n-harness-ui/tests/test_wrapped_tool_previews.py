"""Logical preview budgets must not become physical terminal-row truncation."""

import json
from functools import partial
from itertools import pairwise

import pytest
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.interactive.rows import RowStore
from a13n_harness_ui.interactive.transcript import Transcript, TranscriptControl


def text(transcript, width=40):
    transcript.render(width)
    return "\n".join("".join(fragment[1] for fragment in row) for row in transcript.rows)


def applied(renderer, after, *, path="file.py"):
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "edit", "tool_call_name": "edit"})
    renderer.ingest("TOOL_CALL_END", {"tool_call_id": "edit"})
    renderer.ingest(
        "CUSTOM",
        {
            "name": "a13n.filesystem.edit_applied",
            "value": {"event": {"tool_call_id": "edit", "file_path": path, "before": "", "after": after}},
        },
    )


@pytest.mark.parametrize("count", [99, 100, 101])
def test_edit_diff_budget_counts_logical_body_lines(count):
    renderer = StreamRenderer(Status())
    try:
        applied(renderer, "".join(f"body-{index:02d}\n" for index in range(count)))
        concise = text(renderer.transcript)
        for index in range(min(count, 100)):
            assert f"+body-{index:02d}" in concise
        assert ("more diff lines" in concise) == (count > 100)
        if count == 101:
            assert "+body-100" not in concise
            renderer.transcript.detailed = True
            renderer.transcript.dirty = True
            assert "+body-100" in text(renderer.transcript)
    finally:
        renderer.transcript.close()


@pytest.mark.parametrize("legacy_windows", [False, True])
def test_hundred_long_diff_lines_keep_three_rows_each_and_expand_with_failed_result(legacy_windows, monkeypatch):
    import a13n_harness_ui.interactive.transcript as module

    # Match CI's explicit terminal height, where Rich reserves a legacy column.
    monkeypatch.setenv("LINES", "24")
    monkeypatch.setattr(module, "Console", partial(module.Console, legacy_windows=legacy_windows))
    renderer = StreamRenderer(Status())
    try:
        after = "".join(f"row-{index:02d} " + "wide content " * 15 + f" tail-{index:02d}\n" for index in range(100))
        applied(renderer, after, path="long/path/" * 12 + "unique-file.py")
        renderer.ingest(
            "TOOL_CALL_RESULT", {"tool_call_id": "edit", "content": '{"ok":false,"error":{"message":"failed later"}}'}
        )
        concise = text(renderer.transcript, 30)
        assert len(concise.splitlines()) > 64
        starts = [index for index, row in enumerate(concise.splitlines()) if "+row-" in row]
        assert len(starts) == 100
        assert all(right - left == 3 for left, right in pairwise(starts))
        for index in range(100):
            assert f"tail-{index:02d}" not in concise
        assert "Long diff lines" in concise
        unwrapped = "".join(line.strip("│ ") for line in concise.splitlines())
        assert "unique-file.py" in unwrapped
        assert "Tool result | failed" in concise
        assert "preview shortened" not in concise
        renderer.transcript.detailed = True
        renderer.transcript.dirty = True
        expanded = text(renderer.transcript, 30)
        for index in range(100):
            assert f"tail-{index:02d}" in expanded
        assert "Long diff lines" not in expanded
    finally:
        renderer.transcript.close()


def test_wrapped_preview_pages_are_reused_and_cleaned_on_resize_replace_and_close():
    transcript = Transcript(max_rows=8)
    try:
        block_id = transcript.append("full details", kind="tool")
        transcript.preview(block_id, "Run " + "x" * 900)
        first = text(transcript, 20)
        store = transcript.blocks[block_id].preview_rows
        assert isinstance(store, RowStore)
        assert len(store) > 8 and len(store.pages) <= 2
        text(transcript, 20)
        assert transcript.blocks[block_id].preview_rows is store
        TranscriptControl(transcript).create_content(30, 5)
        assert store.closed and not store.path.exists()
        current = transcript.blocks[block_id].preview_rows
        assert isinstance(current, RowStore)
        assert "x" * 900 == first.replace("\n", "").removeprefix("Run ")
        transcript.replace(block_id, "updated details")
        assert current.closed and not current.path.exists()
        text(transcript, 30)
        final = transcript.blocks[block_id].preview_rows
    finally:
        transcript.close()
    assert isinstance(final, RowStore) and final.closed and not final.path.exists()


def test_wrapped_exploration_rows_keep_later_members_and_expanded_raw_arguments():
    renderer = StreamRenderer(Status())
    try:
        for index, tool in enumerate(("view", "glob")):
            args = {"file_path": "nested/" * 30 + "ending.py"} if tool == "view" else {"pattern": "**/最后的文件.py"}
            call_id = str(index)
            renderer.ingest("TOOL_CALL_START", {"tool_call_id": call_id, "tool_call_name": tool})
            renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": call_id, "delta": json.dumps(args)})
            renderer.ingest("TOOL_CALL_END", {"tool_call_id": call_id})
            renderer.ingest("TOOL_CALL_RESULT", {"tool_call_id": call_id, "content": '{"ok":true}'})
        concise = text(renderer.transcript, 24)
        assert "ending.py" in concise.replace("\n", "")
        assert "最后的文件.py" in concise
        assert len(concise.splitlines()) > 3
        renderer.transcript.detailed = True
        renderer.transcript.dirty = True
        assert "file_path" in text(renderer.transcript, 80)
    finally:
        renderer.transcript.close()


@pytest.mark.parametrize("width", [28, 80])
@pytest.mark.parametrize("prefix", ["+", "-", " "])
@pytest.mark.parametrize("extra", [0, 1])
def test_diff_line_three_row_boundary_and_resize_preserve_retained_source(width, prefix, extra):
    from prompt_toolkit.utils import get_cwidth

    transcript = Transcript()
    body_width = width - 4
    line = prefix + "x" * (3 * body_width - 1 + extra)
    source = f"Edit · file.py · +1 -1\n{line}\n context-tail"
    block_id = transcript.append(source, kind="edit")
    transcript.preview(block_id, source, limit=transcript.block_bytes)
    try:
        for current_width in (width, 400, width):
            rendered = text(transcript, current_width)
            rows = rendered.splitlines()
            assert all(get_cwidth(row) <= current_width for row in rows)
            clipped = extra == 1 and current_width == width
            assert ("…" in rendered) == clipped
            diff_start = next(index for index, row in enumerate(rows) if "xxx" in row)
            next_line = next(index for index, row in enumerate(rows) if "context-tail" in row)
            assert next_line - diff_start == (3 if current_width == width else 1)
            if clipped:
                assert rows[next_line - 1][2:-2].endswith("…")
            assert transcript.blocks[block_id].source == source
        transcript.detailed = True
        transcript.dirty = True
        expanded = text(transcript, width)
        assert "…" not in expanded
        assert expanded.count("x") == line.count("x") + 1  # context-tail
    finally:
        transcript.close()
