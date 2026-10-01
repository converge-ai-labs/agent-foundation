"""Automatic note snapshots stay compact without hiding explicitly requested notes."""

from __future__ import annotations

import json

import pytest
from a13n_harness_ui.interactive.panels import tool_result
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.interactive.theme import resolve_theme
from a13n_harness_ui.surfaces import NotePage, NoteView


def _visible(renderer: StreamRenderer, *, detailed: bool = False, width: int = 120) -> str:
    renderer.transcript.detailed = detailed
    renderer.transcript.dirty = True
    renderer.transcript.render(width)
    return "\n".join("".join(part[1] for part in row) for row in renderer.transcript.rows)


@pytest.mark.parametrize("width", [24, 80, 160])
@pytest.mark.parametrize("theme", ["dark", "light", "ansi"])
def test_note_snapshot_is_one_row_and_expands_without_losing_values(width: int, theme: str) -> None:
    renderer = StreamRenderer(Status())
    renderer.transcript.theme = resolve_theme(theme)
    value = "[bold]literal[/bold]\n" + "long note body\n" * 100 + "FINAL NOTE DETAIL"
    page = NotePage(notes=(NoteView(key="design", value=value),), total=1)
    renderer.restore_notes(page)
    visible = _visible(renderer, width=width)
    assert len(visible.splitlines()) == 1
    assert visible.startswith("Notes · 1 saved")
    assert "long note body" not in visible
    if width >= 80:
        assert "Ctrl+O details · design" in visible
    assert any("bold" in part[0] for row in renderer.transcript.rows for part in row)
    assert value in next(iter(renderer.transcript.blocks.values())).source
    expanded = _visible(renderer, detailed=True, width=width)
    assert "FINAL NOTE DETAIL" in expanded
    renderer.restore_notes(page)
    assert len(renderer.transcript.blocks) == 1
    renderer.transcript.close()


def test_explicit_notes_show_values_even_in_concise_mode_and_report_omissions() -> None:
    renderer = StreamRenderer(Status())
    page = NotePage(notes=(NoteView(key="key", value="Literal saved value"),), total=3, omitted=2)
    renderer.restore_notes(page)
    assert "2 omitted" in _visible(renderer)
    renderer.restore_notes(page, force=True)
    visible = _visible(renderer)
    assert "Literal saved value" in visible
    assert "2 notes omitted" in visible
    assert "Saved values are unchanged" in visible
    renderer.transcript.close()


def test_empty_snapshot_is_quiet_until_requested_or_notes_are_removed() -> None:
    renderer = StreamRenderer(Status())
    renderer.restore_notes(NotePage())
    assert not renderer.transcript.blocks
    renderer.restore_notes(NotePage(), force=True)
    assert "Notes · 0 saved" in _visible(renderer)
    renderer.restore_notes(NotePage(notes=(NoteView(key="key", value="value"),), total=1))
    renderer.restore_notes(NotePage())
    assert _visible(renderer).splitlines()[-1].startswith("Notes · 0 saved")
    renderer.transcript.close()


@pytest.mark.parametrize(
    ("name", "arguments", "result", "label", "key"),
    [
        (
            "note_write",
            {"key": "design", "value": "saved body"},
            {"ok": True, "action": "created"},
            "created",
            "design",
        ),
        (
            "note_write",
            {"key": "design", "value": "saved body"},
            {"ok": True, "action": "updated"},
            "updated",
            "design",
        ),
        ("note_delete", {"key": "design"}, {"ok": True, "action": "deleted"}, "deleted", "design"),
        ("note_delete", {"key": "design"}, {"ok": True, "action": "already_absent"}, "already absent", "design"),
        ("note_get", {"key": "design"}, {"ok": True, "value": "saved body"}, "completed", "design"),
        ("note_get", {"key": None}, {"ok": True, "keys": ["design"], "count": 1}, "completed", "all notes"),
        ("note_write", {"key": "design"}, {"ok": False, "error": {"code": "invalid"}}, "failed", "design"),
    ],
)
def test_note_tools_preview_keys_and_observed_outcomes(name, arguments, result, label, key) -> None:
    renderer = StreamRenderer(Status())
    renderer.ingest("TOOL_CALL_START", {"toolCallId": "note-one", "toolCallName": name})
    renderer.ingest("TOOL_CALL_ARGS", {"toolCallId": "note-one", "delta": json.dumps(arguments)})
    renderer.ingest("TOOL_CALL_END", {"toolCallId": "note-one"})
    assert f"Call {name} {key} …" in _visible(renderer)
    renderer.ingest("TOOL_CALL_RESULT", {"toolCallId": "note-one", "content": json.dumps(result)})
    visible = _visible(renderer)
    assert len(visible.splitlines()) == 1
    assert name in visible and key in visible
    if label != "completed":
        assert label in visible
    else:
        assert "completed" not in visible
    assert "{" not in visible and "saved body" not in visible
    source = next(iter(renderer.transcript.blocks.values())).source
    assert json.dumps(arguments, indent=2) in source
    assert json.dumps(result, indent=2) in source
    assert '"ok"' in _visible(renderer, detailed=True)
    renderer.transcript.close()


def test_note_result_does_not_invent_success_from_unknown_or_failed_payloads() -> None:
    assert tool_result("note_write", '{"action":"created"}').startswith("returned\n")
    assert tool_result("note_write", '{"ok":false,"action":"created"}').startswith("failed\n")
    assert tool_result("note_write", '{"ok":true,"action":{}}').startswith("completed\n")
