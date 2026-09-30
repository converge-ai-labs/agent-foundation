"""Concise presentation retains native authority, correlation, and expandable details."""

from __future__ import annotations

import json

import pytest
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer

from .terminal_display_fixtures import (
    present_block,
    present_context,
    present_native_result,
    present_summary,
    present_text,
    present_tool,
)


@pytest.fixture
def renderer():
    value = StreamRenderer(Status())
    yield value
    value.transcript.close()


def render(renderer, *, detailed=False, width=160):
    renderer.transcript.detailed = detailed
    renderer.transcript.dirty = True
    renderer.transcript.render(width)
    return "\n".join("".join(text for _, text in row) for row in renderer.transcript.rows)


def call(renderer, name, arguments, *, call_id="call-1", run_id="root", result=None, child=False):
    present_tool(
        renderer,
        call_id,
        name=name,
        arguments=json.dumps(arguments),
        arguments_complete=True,
        run_id=run_id,
        child=child,
    )
    if result is not None:
        finish(renderer, call_id, result, run_id=run_id, child=child)


def finish(renderer, call_id, result, *, run_id="root", child=False):
    present_tool(renderer, call_id, run_id=run_id, child=child, result=json.dumps(result))


@pytest.mark.parametrize(
    "name,arguments,expected",
    [
        ("view", {"file_path": "/src/a.py"}, "Read /src/a.py"),
        ("glob", {"pattern": "*.{py,rs}", "root": "src"}, "Find *.{py,rs} in src"),
        ("grep", {"pattern": "foo|bar", "root": "src"}, "Search foo|bar in src"),
        ("ls", {"path": "src"}, "List src"),
        ("mkdir", {"paths": ["src/generated"]}, "Call mkdir src/generated"),
        ("mkdir", {"paths": ["src", "tests"]}, "Call mkdir src, tests"),
        ("unknown", {"input": "value"}, "Call unknown"),
    ],
)
def test_semantic_rows_hide_lifecycle_fields_but_retain_details(renderer, name, arguments, expected):
    call(renderer, name, arguments)
    assert expected in render(renderer)
    assert "…" in render(renderer)
    finish(renderer, "call-1", {"ok": True, "data": "raw-result"})
    concise = render(renderer)
    assert expected in concise
    assert not any(value in concise for value in ("completed", "returned", "call-1", "raw-result", " | "))
    details = render(renderer, detailed=True)
    assert "call-1" in details and "raw-result" in details
    assert render(renderer) == concise


def test_adjacent_reads_deduplicate_full_paths_and_keep_each_call(renderer):
    for index, path in enumerate(["a/file.py", "a/file.py", "b/file.py"]):
        call(renderer, "view", {"file_path": path}, call_id=f"call-{index}", result={"ok": True, "content": str(index)})
    concise = render(renderer)
    assert concise.count("Explored") == 1
    assert concise.count("Read a/file.py") == 1 and concise.count("Read b/file.py") == 1
    details = render(renderer, detailed=True)
    assert all(f"Arguments | call-{index}" in details for index in range(3))


def test_exploration_failure_is_not_deduplicated_or_hidden(renderer):
    call(renderer, "view", {"file_path": "a"}, result={"ok": True})
    call(
        renderer,
        "view",
        {"file_path": "a"},
        call_id="failed",
        result={"ok": False, "error": {"code": "environment_not_found"}},
    )
    concise = render(renderer)
    assert "Read a" in concise
    assert "Read failed: environment_not_found" in concise
    call(
        renderer,
        "grep",
        {"pattern": "("},
        call_id="bad-regex",
        result={"ok": False, "error": {"details": {"field": "pattern", "hint": "Use regex=false for literal text."}}},
    )
    assert "pattern: Use regex=false" in render(renderer, width=70)


def test_group_boundaries_and_late_results_remain_isolated(renderer):
    call(renderer, "view", {"file_path": "a"}, call_id="a")
    call(renderer, "grep", {"pattern": "old"}, call_id="b")
    present_text(renderer, "Boundary answer", message_id="answer")
    call(renderer, "view", {"file_path": "c"}, call_id="c", result={"ok": True})
    finish(renderer, "a", {"ok": True})
    finish(renderer, "b", {"ok": False})
    concise = render(renderer)
    assert concise.index("Read a") < concise.index("Boundary answer") < concise.index("Read c")
    assert "Search failed:" in concise and "Exploring" not in concise
    call(renderer, "view", {"file_path": "other"}, run_id="other", result={"ok": True})
    call(renderer, "view", {"file_path": "child"}, run_id="child", child=True, result={"ok": True})
    assert "Read other" in render(renderer) and "Read child" not in render(renderer)


def test_nonexploration_call_separates_groups(renderer):
    for index, name in enumerate(["view", "glob", "write", "grep", "ls"]):
        call(renderer, name, {"file_path": "a", "pattern": "b", "path": "c"}, call_id=str(index), result={"ok": True})
    assert render(renderer).count("Explored") == 2
    assert "Call write a" in render(renderer)


def test_shell_exit_facts_precede_command_without_output_preview(renderer):
    call(
        renderer,
        "shell_exec",
        {"command": "false | cat"},
        result={"ok": True, "status": {"phase": "exited", "exit_code": 7}, "stdout": {"text": "private output"}},
    )
    concise = render(renderer)
    assert "Run failed · exit 7 · false | cat" in concise
    assert "private output" not in concise
    assert "private output" in render(renderer, detailed=True)


@pytest.mark.parametrize("order", [("summary", "completed"), ("completed", "summary")])
@pytest.mark.parametrize("prefix", ["handoff", "compaction"])
def test_context_completion_and_content_render_one_row_in_any_order(renderer, order, prefix):
    operation_id = prefix + "-1"
    present_context(renderer, operation_id, operation=prefix)
    call(
        renderer,
        "summarize" if prefix == "handoff" else "compact",
        {"summary": "argument text is not authority"},
        result={"ok": True},
    )
    for kind in order:
        if kind == "summary":
            present_summary(renderer, operation_id, "Observed summary body", kind=prefix, files=["/a.py"])
        else:
            present_context(renderer, operation_id, operation=prefix, status="succeeded")
        if kind == "summary" and order[0] == "summary":
            assert "Observed summary body" not in render(renderer)
    concise = render(renderer)
    assert concise.count("Observed summary body") == 1 and concise.count("/a.py") == 1
    assert "argument text" not in concise
    details = render(renderer, detailed=True)
    assert operation_id in details and details.count("Observed summary body") == 1


def test_context_failure_is_terminal_and_children_cannot_supply_root_summary(renderer):
    present_context(
        renderer,
        "handoff-1",
        operation="handoff",
        status="failed",
        error_code="summary_failed",
        failed_phase="apply",
        retryable=False,
    )
    present_summary(renderer, "handoff-1", "Child body", kind="handoff", child=True, run_id="child")
    present_context(
        renderer, "handoff-1", operation="handoff", status="failed", error_code="summary_failed", failed_phase="apply"
    )
    assert "Summary failed: summary_failed" in render(renderer)
    assert "Child body" not in render(renderer)
    assert "apply" in render(renderer, detailed=True)


def test_write_notice_requires_actual_event_is_additive_and_deduplicated(renderer):
    call(renderer, "write", {"file_path": "/argument.txt", "content": "new"}, result={"ok": True})
    assert "Modified:" not in render(renderer)
    event = {
        "payload": {
            "type": "tool_extra",
            "tool_name": "write",
            "tool_id": "filesystem.write",
            "tool_call_id": "call-1",
            "name": "filesystem.changed",
            "value": {"changes": [{"action": "written", "path": "/actual.txt"}]},
        }
    }
    for _ in range(2):
        present_block(
            renderer,
            "write:" + str((event["payload"]).get("tool_call_id")),
            "extension",
            {"event_kind": "tool", "name": "tool_extra", "value": event["payload"]},
        )
    concise = render(renderer)
    assert "Call write /argument.txt" in concise
    assert concise.count("Modified: /actual.txt") == 1
    assert "Modified: /argument.txt" not in concise
    event["payload"]["tool_id"] = "filesystem.edit"
    event["payload"]["tool_call_id"] = "another"
    present_block(
        renderer,
        "write:" + str((event["payload"]).get("tool_call_id")),
        "extension",
        {"event_kind": "tool", "name": "tool_extra", "value": event["payload"]},
    )
    assert render(renderer).count("Modified:") == 1


def test_native_notice_separates_identical_reads(renderer):
    call(renderer, "view", {"file_path": "same"}, result={"ok": True})
    present_block(
        renderer,
        "write:"
        + str(
            (
                {
                    "type": "tool_extra",
                    "tool_id": "filesystem.write",
                    "tool_call_id": "write-1",
                    "name": "filesystem.changed",
                    "value": {"changes": [{"action": "written", "path": "/same"}]},
                }
            ).get("tool_call_id")
        ),
        "extension",
        {
            "event_kind": "tool",
            "name": "tool_extra",
            "value": {
                "type": "tool_extra",
                "tool_id": "filesystem.write",
                "tool_call_id": "write-1",
                "name": "filesystem.changed",
                "value": {"changes": [{"action": "written", "path": "/same"}]},
            },
        },
    )
    call(renderer, "view", {"file_path": "same"}, call_id="after", result={"ok": True})
    concise = render(renderer)
    assert concise.count("Read same") == 2
    assert concise.index("Read same") < concise.index("Modified:") < concise.rindex("Read same")


def test_evicted_group_anchor_does_not_hide_retained_failure(renderer):
    renderer.transcript.max_blocks = 3
    for index in range(3):
        call(
            renderer,
            "view",
            {"file_path": str(index)},
            call_id=str(index),
            result={"ok": False, "error": {"code": "environment_not_found"}} if index == 1 else {"ok": True},
        )
    present_text(renderer, "answer", message_id="answer")
    concise = render(renderer)
    assert "environment_not_found" in concise and "Read 2" in concise
    assert "Arguments | 1" in render(renderer, detailed=True)


def test_read_deduplication_uses_full_not_truncated_path(renderer):
    for index in range(2):
        call(renderer, "view", {"file_path": "directory/" * 60 + str(index)}, call_id=str(index), result={"ok": True})
    assert render(renderer).count("Read ") == 2


def test_context_validation_failure_without_lifecycle_remains_visible(renderer):
    call(renderer, "summarize", {"content": ""})
    present_native_result(
        renderer,
        {
            "tool_name": "summarize",
            "tool_call_id": "call-1",
            "part_kind": "retry-prompt",
            "content": "content must not be empty",
        },
    )
    assert "retry:" in render(renderer)
    assert "content must not be empty" in render(renderer, detailed=True)


def test_mkdir_preview_shortens_paths_and_discloses_omitted_targets(renderer, tmp_path):
    renderer.status.directory = tmp_path
    paths = [str(tmp_path / name) for name in ("first", "second", "third", "fourth")]
    call(renderer, "mkdir", {"paths": paths}, result={"ok": True})
    assert render(renderer) == "Call mkdir first, second, third (+1 more)"
    details = render(renderer, detailed=True, width=4096)
    assert all(json.dumps(path) in details for path in paths)


def test_mkdir_failure_keeps_target_and_does_not_claim_creation(renderer):
    call(renderer, "mkdir", {"paths": ["target"]}, result={"ok": False, "error": {"code": "environment_denied"}})
    visible = render(renderer)
    assert "environment_denied" in visible and "Call mkdir target" in visible
    assert "created" not in visible


def test_bounded_file_read_remains_successful_and_keeps_recovery_details(renderer):
    call(
        renderer,
        "view",
        {"file_path": "SKILL.md", "line_limit": 1000},
        result={
            "ok": True,
            "content": "bounded source prefix\n",
            "line_offset": 0,
            "lines_read": 1,
            "has_more": True,
            "next_line_offset": 1,
            "disclosure": {"truncated": True, "hint": "Use next_line_offset to continue."},
        },
    )
    assert render(renderer) == "Read SKILL.md"
    details = render(renderer, detailed=True)
    assert "bounded source prefix" in details and "Use next_line_offset" in details
