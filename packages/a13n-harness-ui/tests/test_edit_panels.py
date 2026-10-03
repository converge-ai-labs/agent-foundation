"""Edit-only terminal projection omits metadata, not source content."""

from __future__ import annotations

import json

import pytest
from a13n_harness_ui.interactive.panels import capability_panel, shell_result_preview, tool_result
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer

from .display_fixture import feed_display


@pytest.mark.parametrize(
    "before, after, counts, body",
    [
        ("context\nold\ntail\n", "context\nnew\ntail\n", "+1 -1", " context\n-old\n+new\n tail\n"),
        ("", "new\n", "+1 -0", "+new\n"),
        ("old\n", "", "+0 -1", "-old\n"),
        ("", "", "+0 -0", "Empty file created.\n"),
        ("x", "x\n", "+1 -1", "-x\n\\ No newline at end of file\n+x\n"),
        ("x\n", "x", "+1 -1", "-x\n+x\n\\ No newline at end of file\n"),
        ("-- old\n@@ source\n", "++ new\n@@ source\n", "+1 -1", "--- old\n+++ new\n @@ source\n"),
    ],
)
def test_edit_body_keeps_changes_without_generated_headers(before: str, after: str, counts: str, body: str) -> None:
    event = {"file_path": "file.py", "before": before, "after": after}
    original = event.copy()
    panel = capability_panel("a13n.filesystem.edit_applied", event)
    assert panel is not None
    assert panel.title == f"Edit · file.py · {counts}"
    assert panel.body == body
    assert event == original


def test_disjoint_edit_hunks_keep_context_and_a_separator_without_coordinates() -> None:
    before = "".join(f"line-{i}\n" for i in range(20))
    after = before.replace("line-1\n", "first\n").replace("line-18\n", "last\n")
    panel = capability_panel("a13n.filesystem.edit_applied", {"file_path": "file.py", "before": before, "after": after})
    assert panel is not None
    assert panel.title == "Edit · file.py · +2 -2"
    assert panel.body == (
        " line-0\n-line-1\n+first\n line-2\n line-3\n line-4\n"
        "…\n"
        " line-15\n line-16\n line-17\n-line-18\n+last\n line-19\n"
    )


@pytest.mark.parametrize("before", ["original\n" * 2001, "x" * 140_000], ids=["many-lines", "long-line"])
def test_edit_within_expanded_comparison_budget_still_renders_diff(before: str) -> None:
    panel = capability_panel(
        "a13n.filesystem.edit_applied", {"file_path": "file.py", "before": before, "after": before + "last"}
    )
    assert panel is not None and panel.kind == "edit"
    assert "last" in panel.body
    assert "omitted" not in panel.title


@pytest.mark.parametrize(
    "before", ["original\n" * 5001, "x" * (256 * 1024 + 1)], ids=["too-many-lines", "too-many-characters"]
)
def test_edit_over_comparison_budget_returns_a_plain_fact_with_raw_details(before: str) -> None:
    event = {"file_path": "file.py", "before": before, "after": before + "last"}
    panel = capability_panel("a13n.filesystem.edit_applied", event)
    assert panel is not None
    assert panel.kind == "tool"
    assert panel.title == "Modified: file.py · diff preview omitted (size limit)"
    assert json.loads(panel.body) == event


def test_oversized_edit_replaces_call_with_one_borderless_fact_and_preserves_details() -> None:
    renderer = StreamRenderer(Status())
    feed_display(renderer, "TOOL_CALL_START", {"toolCallId": "large", "toolCallName": "edit"})
    feed_display(renderer, "TOOL_CALL_ARGS", {"toolCallId": "large", "delta": '{"file_path":"file.py"}'})
    feed_display(renderer, "TOOL_CALL_END", {"toolCallId": "large"})
    before = "x\n" * 6000
    feed_display(
        renderer,
        "CUSTOM",
        {
            "name": "a13n.filesystem.edit_applied",
            "value": {
                "event": {"tool_call_id": "large", "file_path": "file.py", "before": before, "after": before + "last"}
            },
        },
    )
    feed_display(renderer, "TOOL_CALL_RESULT", {"toolCallId": "large", "content": '{"ok":true}'})
    try:
        renderer.transcript.render(100)
        text = "\n".join("".join(text for _, text in row).rstrip() for row in renderer.transcript.rows)
        assert text == "Modified: file.py · diff preview omitted (size limit)"
        assert len(renderer.transcript.blocks) == 1
        block = next(iter(renderer.transcript.blocks.values()))
        assert block.kind == "tool" and "Tool result · large" in block.source
        assert '"before":' in block.source and '"after":' in block.source
        assert "Actual before/after" not in block.source
    finally:
        renderer.transcript.close()


@pytest.mark.parametrize("detailed", [False, True])
def test_rendered_edit_panel_shows_only_one_path_and_no_hunk_coordinates(detailed: bool) -> None:
    renderer = StreamRenderer(Status())
    feed_display(
        renderer,
        "CUSTOM",
        {
            "name": "a13n.filesystem.edit_applied",
            "value": {"event": {"file_path": "file.py", "before": "context\nold\n", "after": "context\nnew\n"}},
        },
    )
    try:
        renderer.transcript.detailed = detailed
        renderer.transcript.render(80)
        text = "\n".join("".join(text for _, text in row) for row in renderer.transcript.rows)
        assert text.count("file.py") == 1
        assert "Edit · file.py · +1 -1" in text
        assert " context" in text and "-old" in text and "+new" in text
        assert "@@" not in text
        assert any("ansigreen" in style for row in renderer.transcript.rows for style, _ in row)
        assert any("ansired" in style for row in renderer.transcript.rows for style, _ in row)
    finally:
        renderer.transcript.close()


def test_shell_diff_output_retains_file_headers_and_hunk_coordinates() -> None:
    diff = "--- file.py\n+++ file.py\n@@ -1 +1 @@\n-old\n+new\n"
    result = json.dumps({"status": {"phase": "exited", "exit_code": 0}, "stdout": {"text": diff}})
    assert shell_result_preview(result, "git diff") == "exit 0 | git diff"
    _, _, expanded = tool_result("shell_exec", result).partition("\n")
    assert json.loads(expanded)["stdout"]["text"] == diff


@pytest.mark.parametrize("detailed", [False, True])
@pytest.mark.parametrize("theme", ["auto", "dark", "light"])
@pytest.mark.parametrize(
    "path",
    [
        "src/file.py",
        "/Users/example/.a13n-harness-ui/data/threads/thread-123456789/tmp/tool-smoke-test-0547c555.txt",
        r"C:\Users\example\project\long-directory\file.py",
        "src/中文目录/文件 · [bold].py",
    ],
)
def test_edit_heading_reflows_between_border_and_subdued_path_without_losing_content(path, theme, detailed) -> None:
    from a13n_harness_ui.interactive.theme import activity_colors, resolve_theme
    from prompt_toolkit.utils import get_cwidth

    renderer = StreamRenderer(Status())
    renderer.transcript.theme = resolve_theme(theme)
    renderer.transcript.detailed = detailed
    feed_display(
        renderer,
        "CUSTOM",
        {
            "name": "a13n.filesystem.edit_applied",
            "value": {"event": {"file_path": path, "before": "old\n", "after": "new\n"}},
        },
    )
    title = f"Edit · {path} · +1 -1"
    fitting_width = get_cwidth(title) + 6
    block = next(iter(renderer.transcript.blocks.values()))
    source = block.source
    try:
        # Cover the exact fit boundary, narrow fallback, and resize back to a title.
        for width in (fitting_width, fitting_width - 1, 28, 12, fitting_width):
            renderer.transcript.render(width)
            rows = ["".join(text for _, text in row) for row in renderer.transcript.rows]
            assert all(get_cwidth(row) <= width for row in rows)
            assert rows[0].startswith(("╭", "┌")) and rows[-1].startswith(("╰", "└"))
            body = "".join(row[2:-2].rstrip() for row in rows[1:-1])
            assert "-old" in body and "+new" in body
            if width == fitting_width:
                assert title in rows[0]
                assert path not in body
                assert "-old" in rows[1]
            else:
                # Wrapping can put path whitespace at padded line ends.
                assert path.replace(" ", "") in body.replace(" ", "")
                assert "Edit" in rows[0] and "Edit" not in body
                assert ("+1 -1" in rows[0]) if width >= 18 else ("+1 -1" in body)
                diff_start = next(index for index, row in enumerate(rows) if "-old" in row)
                assert not rows[diff_start - 1][2:-2].strip()
                muted = activity_colors(renderer.transcript.theme)["muted"]
                muted = muted if muted.startswith("#") else "ansi" + muted.replace("_", "")
                assert any(
                    f"fg:{muted}" in style and "bold" not in style.split()
                    for row in list(renderer.transcript.rows)[1 : diff_start - 1]
                    for style, text in row
                    if text.strip("│ ")
                )
            assert any("ansigreen" in style for row in renderer.transcript.rows for style, _ in row)
            assert any("ansired" in style for row in renderer.transcript.rows for style, _ in row)
            assert block.source == source
    finally:
        renderer.transcript.close()
