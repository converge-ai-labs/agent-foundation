"""Edit-only terminal projection omits metadata, not source content."""

from __future__ import annotations

import json

import pytest
from a13n_harness_ui.interactive.panels import capability_panel, shell_result_preview, tool_result
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer


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


def test_edit_over_comparison_budget_labels_actual_text_without_repeating_path() -> None:
    before = "original\n" * 2001
    after = before + "last"
    panel = capability_panel("a13n.filesystem.edit_applied", {"file_path": "file.py", "before": before, "after": after})
    assert panel is not None
    assert panel.title == "Edit · file.py · applied"
    assert panel.body == (
        "Unified diff omitted: comparison budget exceeded. Actual before/after text follows.\n"
        f"Before:\n{before}\nAfter:\n{after}"
    )


@pytest.mark.parametrize("detailed", [False, True])
def test_rendered_edit_panel_shows_only_one_path_and_no_hunk_coordinates(detailed: bool) -> None:
    renderer = StreamRenderer(Status())
    renderer.ingest(
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
