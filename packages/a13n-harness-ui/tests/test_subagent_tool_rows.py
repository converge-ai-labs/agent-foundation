"""Subagent rows show instructions and return facts without claiming live state."""

from __future__ import annotations

import json

import pytest
from a13n_harness_ui.interactive.rendering import terminal_text
from a13n_harness_ui.interactive.tool_rows import semantic_tool_row, subagent_result_row


@pytest.mark.parametrize(
    "name,arguments,expected",
    [
        (
            "delegate",
            {"subagent_name": "reviewer", "prompt": "Review the API\n\tand add tests."},
            "Delegate to reviewer\n  Review the API and add tests.",
        ),
        (
            "steer_subagent",
            {"execution_id": "exec-123", "message": "Focus on\n cancellation."},
            "Steer exec-123\n  Focus on cancellation.",
        ),
    ],
)
def test_subagent_call_rows_include_target_and_instruction(name, arguments, expected):
    assert semantic_tool_row(name, json.dumps(arguments)) == (expected, None)


@pytest.mark.parametrize("name,key", [("delegate", "prompt"), ("steer_subagent", "message")])
@pytest.mark.parametrize("length", [180, 500, 501, 2000])
def test_instruction_excerpt_is_generous_bounded_and_discloses_omission(name, key, length):
    arguments = {"subagent_name": "reviewer", "execution_id": "exec-123", key: "x" * length}
    row, _ = semantic_tool_row(name, json.dumps(arguments))
    instruction = row.split("\n", 1)[1].removeprefix("  ")
    assert instruction == ("x" * length if length <= 500 else "x" * 499 + "…")


@pytest.mark.parametrize("arguments", ["", "{", "[]", "null", '{"subagent_name": [], "prompt": 7}'])
def test_malformed_delegate_arguments_do_not_invent_instruction_or_identity(arguments):
    assert semantic_tool_row("delegate", arguments) == ("Delegate to subagent unavailable", None)


def test_missing_steer_arguments_do_not_invent_target_or_instruction():
    assert semantic_tool_row("steer_subagent", "{}") == ("Steer target unavailable", None)


@pytest.mark.parametrize("status", ["running", "succeeded", "failed", "cancelled", "lost"])
def test_delegate_result_reports_only_observed_status_at_return(status):
    semantic = "Delegate to reviewer\n  Review the API."
    result = {"execution_id": "exec-123", "status": status, "output": "not previewed"}
    assert subagent_result_row("delegate", semantic, json.dumps(result)) == (
        f"Delegate to reviewer · exec-123 · {status} at return\n  Review the API."
    )


def test_inline_delegate_identity_does_not_invent_missing_status():
    result = {"execution_id": "exec-inline", "subagent": "reviewer", "output": "done"}
    assert subagent_result_row("delegate", "Delegate to reviewer", json.dumps(result)) == (
        "Delegate to reviewer · exec-inline"
    )


@pytest.mark.parametrize("accepted,expected", [(True, "accepted for delivery"), (False, "not accepted")])
def test_steering_acknowledgement_is_not_child_consumption_or_completion(accepted, expected):
    semantic = "Steer exec-123\n  Focus on cancellation."
    result = {"execution_id": "exec-123", "accepted": accepted, "enqueue_id": "enqueue-1"}
    assert subagent_result_row("steer_subagent", semantic, json.dumps(result)) == (
        f"Steer exec-123 · {expected}\n  Focus on cancellation."
    )


@pytest.mark.parametrize("name", ["delegate", "steer_subagent"])
@pytest.mark.parametrize(
    "result",
    ["", "{", "[]", "null", '"accepted"', "{}", '{"execution_id": 7}', '{"ok": true}'],
)
def test_unknown_or_malformed_result_does_not_manufacture_acknowledgement(name, result):
    assert subagent_result_row(name, "Steer exec-123", result) is None


@pytest.mark.parametrize("accepted", [None, "true", "false", 1, 0, [], {}])
def test_steer_requires_a_real_boolean_acknowledgement(accepted):
    result = {"execution_id": "exec-123", "accepted": accepted}
    assert subagent_result_row("steer_subagent", "Steer exec-123", json.dumps(result)) is None


def test_steer_does_not_acknowledge_a_different_execution():
    result = {"execution_id": "exec-other", "accepted": True}
    assert subagent_result_row("steer_subagent", "Steer exec-123", json.dumps(result)) is None


@pytest.mark.parametrize("status", ["completed", "accepted", True, [], {}])
def test_unknown_delegate_status_does_not_become_success(status):
    result = {"execution_id": "exec-123", "status": status}
    assert subagent_result_row("delegate", "Delegate to reviewer", json.dumps(result)) is None


@pytest.mark.parametrize("name", ["delegate", "steer_subagent"])
@pytest.mark.parametrize("failure", [{"ok": False}, {"error": {"code": "denied"}}])
def test_native_failure_is_not_replaced_by_result_enrichment(name, failure):
    result = {"execution_id": "exec-123", "status": "running", "accepted": True, **failure}
    assert subagent_result_row(name, "Steer exec-123", json.dumps(result)) is None


def test_other_tools_are_not_enriched():
    result = {"execution_id": "exec-123", "status": "running"}
    assert subagent_result_row("subagent_info", "Call subagent_info", json.dumps(result)) is None


@pytest.mark.parametrize("name", ["delegate", "steer_subagent"])
def test_rows_use_existing_renderer_sanitization_boundary(name):
    arguments = {
        "subagent_name": "reviewer\x07",
        "execution_id": "exec-123\x07",
        "prompt": "Inspect \x1b[31mred\x1b[0m\x00 text.",
        "message": "Inspect \x1b[31mred\x1b[0m\x00 text.",
    }
    semantic, _ = semantic_tool_row(name, json.dumps(arguments))
    result = {"execution_id": "exec-123\x07", "status": "running", "accepted": True}
    enriched = subagent_result_row(name, semantic, json.dumps(result))
    assert enriched is not None
    for row in (semantic, enriched):
        sanitized = terminal_text(row)
        assert "\n  Inspect" in sanitized
        assert not any(char in sanitized for char in "\x00\x07\x1b")


@pytest.mark.parametrize(
    "name,key,prefix",
    [("view", "file_path", "Read "), ("ls", "path", "List "), ("shell_exec", "command", "Run ")],
)
def test_existing_semantic_limits_disclose_omission_and_preserve_read_identity(name, key, prefix):
    target = "x" * 600
    row, read_path = semantic_tool_row(name, json.dumps({key: target}))
    assert row == prefix + "x" * 499 + "…"
    assert read_path == (target if name == "view" else None)


def test_search_pattern_and_root_limits_each_disclose_omission():
    row, _ = semantic_tool_row("grep", json.dumps({"pattern": "x" * 600, "root": "y" * 600}))
    assert row == "Search " + "x" * 499 + "… in " + "y" * 499 + "…"


@pytest.mark.parametrize("native_first", [True, False])
@pytest.mark.parametrize("name", ["delegate", "steer_subagent"])
def test_streamed_subagent_receipts_wrap_and_deduplicate_native_protocol_results(name, native_first):
    from a13n_harness_ui.interactive.rendering import Status, StreamRenderer

    renderer = StreamRenderer(Status())
    arguments = {
        "subagent_name": "reviewer",
        "execution_id": "exec-123",
        "prompt": "Inspect " + "wrapped tasks " * 12,
        "message": "Focus on " + "wrapped guidance " * 12,
    }
    result = (
        {"execution_id": "exec-123", "status": "running"}
        if name == "delegate"
        else {"execution_id": "exec-123", "accepted": True}
    )
    part = {
        "part_kind": "tool-return",
        "tool_name": name,
        "tool_call_id": "call",
        "outcome": "success",
        "content": result,
    }
    native = ("CUSTOM", {"name": "a13n.pydantic_ai.function_tool_result", "value": {"event": {"part": part}}})
    protocol = ("TOOL_CALL_RESULT", {"tool_call_id": "call", "content": json.dumps(result)})
    try:
        renderer.ingest("TOOL_CALL_START", {"tool_call_id": "call", "tool_call_name": name})
        renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "call", "delta": json.dumps(arguments)})
        renderer.ingest("TOOL_CALL_END", {"tool_call_id": "call"})
        renderer.transcript.render(24)
        pending = "\n".join("".join(text for _, text in row) for row in renderer.transcript.rows)
        assert "wrapped" in pending and "…" in pending
        assert "at return" not in pending and "accepted" not in pending
        events = [native, protocol] if native_first else [protocol, native]
        for event in events + events:
            renderer.ingest(*event)
        assert len(renderer.transcript.blocks) == 1
        renderer.transcript.render(24)
        visible = "\n".join("".join(text for _, text in row) for row in renderer.transcript.rows)
        assert "wrapped" in visible and "exec-123" in visible
        assert ("at return" if name == "delegate" else "accepted for delivery") in " ".join(visible.split())
        assert "Call tool" not in visible and len(visible.splitlines()) > 4
        source = next(iter(renderer.transcript.blocks.values())).source
        assert "part_kind" in source and "Additional tool result" in source
        assert "wrapped tasks" in source or "wrapped guidance" in source
    finally:
        renderer.transcript.close()


def test_native_subagent_failure_is_not_overwritten_by_protocol_success():
    from a13n_harness_ui.interactive.rendering import Status, StreamRenderer

    renderer = StreamRenderer(Status())
    try:
        renderer.ingest("TOOL_CALL_START", {"tool_call_id": "call", "tool_call_name": "steer_subagent"})
        renderer.ingest(
            "TOOL_CALL_ARGS", {"tool_call_id": "call", "delta": '{"execution_id":"exec-123","message":"new guidance"}'}
        )
        renderer.ingest("TOOL_CALL_END", {"tool_call_id": "call"})
        part = {
            "part_kind": "tool-return",
            "tool_name": "steer_subagent",
            "tool_call_id": "call",
            "outcome": "denied",
            "content": "not allowed",
        }
        renderer.ingest("CUSTOM", {"name": "a13n.pydantic_ai.function_tool_result", "value": {"event": {"part": part}}})
        renderer.ingest(
            "TOOL_CALL_RESULT", {"tool_call_id": "call", "content": '{"execution_id":"exec-123","accepted":true}'}
        )
        block = next(iter(renderer.transcript.blocks.values()))
        assert "denied" in block.preview and "accepted for delivery" not in block.preview
        assert len(renderer.transcript.blocks) == 1
    finally:
        renderer.transcript.close()
