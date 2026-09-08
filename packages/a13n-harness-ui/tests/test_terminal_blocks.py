"""Full semantic blocks, paged layout, and explicit regional interaction."""

from __future__ import annotations

import json

from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.panels import capability_panel
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.interactive.selection import Choice, Selection
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.interactive.theme import prompt_toolkit_style_rules, resolve_theme
from a13n_harness_ui.interactive.transcript import Transcript, TranscriptControl
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Point
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.output import DummyOutput


def _source(renderer: StreamRenderer) -> str:
    return "\n".join(block.source for block in renderer.transcript.blocks.values())


def _text(transcript: Transcript) -> str:
    return "\n".join("".join(fragment[1] for fragment in row) for row in transcript.rows)


def _tool(renderer: StreamRenderer, name: str, args: dict, result: str = "done") -> None:
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "call-1", "tool_call_name": name})
    arguments = json.dumps(args)
    for start in range(0, len(arguments), 127):
        renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "call-1", "delta": arguments[start : start + 127]})
    renderer.ingest("TOOL_CALL_END", {"tool_call_id": "call-1"})
    renderer.ingest("TOOL_CALL_RESULT", {"tool_call_id": "call-1", "content": result})


def test_thinking_is_separate_and_expanded_in_concise_mode() -> None:
    renderer = StreamRenderer(Status())
    renderer.ingest("THINKING_TEXT_MESSAGE_CONTENT", {"message_id": "one", "delta": "exposed reasoning"})
    renderer.ingest("TEXT_MESSAGE_CONTENT", {"message_id": "one", "delta": "answer"})
    blocks = list(renderer.transcript.blocks.values())
    assert len(blocks) == 2
    assert blocks[0].source == "exposed reasoning"
    assert blocks[0].collapsed_lines is None
    assert blocks[1].source == "answer"


def test_edit_diff_and_summary_keep_content_beyond_old_preview_limit() -> None:
    renderer = StreamRenderer(Status())
    long = "before\n" * 1800 + "LAST ORIGINAL LINE\n"
    _tool(
        renderer,
        "multi_edit",
        {"file_path": "sample.txt", "edits": [{"old_string": long, "new_string": "new\n", "replace_all": True}]},
        '{"ok":false,"error":{"code":"ambiguous"}}',
    )
    source = _source(renderer)
    assert "proposed replacement snippets" not in source
    assert "Edit · applied" not in source
    assert "LAST ORIGINAL LINE" not in source
    assert "failed · no edit confirmed" in source
    summary = "A full summary.\n" * 1000 + "SUMMARY END"
    renderer.ingest(
        "CUSTOM",
        {
            "name": "a13n.context.handoff_summary",
            "value": {
                "event": {
                    "summary": summary,
                    "files": ["a.py"],
                    "operation_id": "handoff-one",
                }
            },
        },
    )
    assert summary in _source(renderer)
    assert "Files to inspect:\na.py" in _source(renderer)


def test_edit_diff_preserves_newline_only_change() -> None:
    panel = capability_panel("a13n.filesystem.edit_applied", {"file_path": "a.py", "before": "x", "after": "x\n"})
    assert panel is not None
    assert "-x\n\\ No newline at end of file\n+x" in panel.body


def test_large_edit_bypasses_unbounded_diff_work(monkeypatch) -> None:
    import a13n_harness_ui.interactive.panels as panels

    def forbidden(*args, **kwargs):
        raise AssertionError("Large edits must not enter quadratic diff matching")

    monkeypatch.setattr(panels.difflib, "unified_diff", forbidden)
    before = "".join(f"line {index % 100}\n" for index in range(80000))
    panel = capability_panel(
        "a13n.filesystem.edit_applied", {"file_path": "a.py", "before": before, "after": before + "last"}
    )
    assert panel is not None
    assert "comparison budget exceeded" in panel.body
    assert before in panel.body and panel.body.endswith("last")


def test_regular_tool_folds_without_discarding_full_result() -> None:
    renderer = StreamRenderer(Status())
    _tool(renderer, "read", {"file_path": "a.py"}, "\n".join(f"line {i}" for i in range(100)))
    transcript = renderer.transcript
    transcript.render(80)
    assert "Folded" not in _text(transcript)
    assert len(transcript.blocks) == 1
    assert "read · result" in _text(transcript)
    assert "line 99" not in _text(transcript)
    assert "line 99" in _source(renderer)
    transcript.detailed = True
    transcript.dirty = True
    transcript.render(80)
    assert "line 99" in _text(transcript)
    assert "Folded" not in _text(transcript)
    transcript.close()


def test_context_lifecycle_is_full_and_never_claims_hidden_summary() -> None:
    renderer = StreamRenderer(Status())
    mutation = {
        "type": "compaction_failed",
        "operation_id": "compact-1",
        "failed_phase": "nested_run",
        "retryable": False,
        "error_code": "failure",
    }
    renderer.ingest("CUSTOM", {"name": "a13n.harness.context", "value": {"event": {"payload": mutation}}})
    source = _source(renderer)
    assert "[Compact · root]" in source
    assert all(key in source for key in mutation)
    assert "reduction" not in source


def test_compaction_summary_projects_to_an_independent_expanded_block() -> None:
    from datetime import UTC, datetime

    from a13n_harness import HarnessEvent
    from a13n_harness.capabilities import CompactionSummaryEvent
    from a13n_stream_protocol import HarnessAguiObserver

    summary = "Retain every summary line.\n" * 3000 + "FINAL SUMMARY LINE"
    source = HarnessEvent(
        thread_id="thread-1",
        run_id="run-1",
        sequence=1,
        occurred_at=datetime.now(UTC),
        event=CompactionSummaryEvent(operation_id="compaction-1", summary=summary),
    )
    renderer = StreamRenderer(Status())
    for event in HarnessAguiObserver().observe(source):
        renderer.ingest(event.type.value, event.model_dump(mode="json"), run_id="run-1")
    blocks = list(renderer.transcript.blocks.values())
    assert blocks[0].source == "Compact summary\n" + summary + "\n"
    assert blocks[0].kind == "compact"
    assert blocks[0].collapsed_lines is None
    assert not renderer.assistant_seen


def test_steering_delivery_notice_does_not_duplicate_the_input_event_body() -> None:
    renderer = StreamRenderer(Status())
    renderer.ingest(
        "CUSTOM",
        {
            "name": "a13n.pydantic_ai.enqueued_messages",
            "value": {
                "event": {
                    "event_kind": "enqueued_messages",
                    "enqueue_id": "enqueue-123",
                    "messages": [{"parts": [{"part_kind": "user-prompt", "content": "change direction"}]}],
                }
            },
        },
    )
    assert _source(renderer) == ""
    assert "change direction" not in _source(renderer)


def test_long_message_pages_rehydrate_without_4000_row_cutoff() -> None:
    transcript = Transcript()
    source = "\n".join(f"row {i:05d}" for i in range(12000))
    block_id = transcript.append(source)
    transcript.render(80)
    assert "Display truncated" not in transcript.blocks[block_id].source
    assert len(transcript.rows) > 12000
    assert "row 00000" in str(transcript.rows[0])
    assert "row 11999" in str(transcript.rows[-2])
    view = TranscriptControl(transcript)
    view.create_content(80, 20)
    view.scroll(-10000)
    view.create_content(80, 20)
    assert not view.follow
    view.scroll(20000)
    assert view.follow
    transcript.close()


def test_large_stream_reuses_stable_chunks_then_reflows_final_markdown() -> None:
    transcript = Transcript()
    block_id = transcript.append("# Title\n\n" + "streaming line\n" * 3000, markdown=True, streaming=True)
    transcript.render(80)
    block = transcript.blocks[block_id]
    stable = block.chunks[0][1]
    transcript.extend(block_id, "final tail")
    transcript.render(80)
    assert block.chunks[0][1] is stable
    transcript.complete(block_id)
    transcript.render(80)
    assert not block.chunks
    assert stable.closed
    text = _text(transcript)
    assert "Title" in text and "final tail" in " ".join(text.split())
    assert any("bold" in style for style, text in transcript.rows[0])
    transcript.close()


def test_selector_wheel_only_scrolls_and_click_only_highlights() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.selection = Selection(tuple(Choice(str(i), f"Option {i}") for i in range(30)), cursor=0)
        shell._panel_mouse(MouseEvent(Point(0, 2), MouseEventType.SCROLL_DOWN, MouseButton.NONE, frozenset()))
        assert shell.selection.cursor == 0
        assert shell.selection.view_start == 3
        assert shell.view.follow
        shell._panel_mouse(MouseEvent(Point(0, 2), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
        assert shell.selection.cursor == 4
        assert shell.selection.checked == set()
        assert shell.selector_focused
        assert shell.mouse


def test_native_theme_and_context_percentage_are_truthful() -> None:
    rules = prompt_toolkit_style_rules(resolve_theme("auto", environ={}))
    assert rules[""] == "bg:default fg:default"
    assert "#" not in " ".join(rules.values())
    assert "ctx --" in Status(context_window=100).line()
    assert "ctx 0 (0%)" in Status(context_window=100, context_tokens=0).line()
    assert "ctx 38 (38%)" in Status(context_window=100, context_tokens=38).line()
    assert "Cancelling" in Status(state="cancelling").line(20)


def test_multiline_option_text_remains_one_click_target_row() -> None:
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.selection = Selection(
            (Choice("a", "A", "first line\nsecond line"), Choice("b", "B"), Choice("c", "C")), cursor=0
        )
        panel = "".join(fragment[1] for fragment in shell._panel())
        assert "first line second line" in panel.splitlines()[1]
        assert "2. B" in panel.splitlines()[2]
        shell._panel_mouse(MouseEvent(Point(2, 2), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
        assert shell.selection.answer() == "2"


def test_render_cache_does_not_hold_one_descriptor_per_block() -> None:
    import os
    import subprocess
    import sys

    import pytest

    if os.name == "nt":
        pytest.skip("POSIX resource limits")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import resource
from a13n_harness_ui.interactive.transcript import Transcript
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
limit = 128 if hard == resource.RLIM_INFINITY else min(128, hard)
resource.setrlimit(resource.RLIMIT_NOFILE, (limit, hard))
assert resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= 128
t = Transcript()
for i in range(400):
    t.append(f'block {i}')
t.render(80)
assert 'block 0' in str(t.rows[0])
assert 'block 399' in str(t.rows[-2])
t.render(40)
t.close()
""",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_input_events_hide_context_without_marking_user_as_assistant() -> None:
    from datetime import UTC, datetime

    from a13n_harness import HarnessEvent
    from a13n_harness.model_context import ModelInputEvent
    from a13n_stream_protocol import HarnessAguiObserver
    from pydantic_ai.messages import TextContent

    renderer = StreamRenderer(Status(state="running", mode="detailed"))
    source = HarnessEvent(
        thread_id="thread-input",
        run_id="run-input",
        sequence=0,
        occurred_at=datetime.now(UTC),
        event=ModelInputEvent(
            content=[
                TextContent("AGENTS.md user question"),
                TextContent("HIDDEN GUIDANCE", metadata={"display": False, "source_id": "test"}),
            ]
        ),
    )
    for event in HarnessAguiObserver().observe(source):
        payload = event.model_dump(mode="json")
        renderer.ingest(payload["type"], payload, run_id="run-input")
    assert _source(renderer) == "> AGENTS.md user question"
    assert "HIDDEN" not in renderer.drain()
    assert not renderer.assistant_seen
    assert renderer.status.state == "running"
    # CONTENT remains safe even after a bounded consumer loses START.
    renderer.ingest(
        "TEXT_MESSAGE_CONTENT",
        {
            "message_id": "lost-start",
            "role": "user",
            "delta": "HIDDEN",
            "metadata": {"display": False},
        },
    )
    assert "HIDDEN" not in _source(renderer)


def test_native_shell_preview_prioritizes_command_output_and_failure_with_exit_code() -> None:
    import json

    renderer = StreamRenderer(Status())
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "shell-one", "tool_call_name": "shell_exec"})
    renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "shell-one", "delta": '{"command":"pytest -q"}'})
    renderer.ingest("TOOL_CALL_END", {"tool_call_id": "shell-one"})
    renderer.ingest(
        "TOOL_CALL_RESULT",
        {
            "tool_call_id": "shell-one",
            "content": json.dumps(
                {
                    "ok": True,
                    "status": {"phase": "exited", "exit_code": 7},
                    "stderr": {"text": "test failure", "coverage": "partial", "content_complete": False},
                    "stdout": {"text": "test output", "coverage": "complete"},
                }
            ),
        },
    )
    renderer.transcript.render(80)
    text = _text(renderer.transcript)
    assert "shell_exec · failed · exit 7 · pytest -q" in text
    assert "exited" not in text
    assert text.index("test failure") < text.index("test output")
    assert "output partial" in text
    renderer.transcript.detailed = True
    renderer.transcript.dirty = True
    renderer.transcript.render(80)
    assert '"exit_code": 7' in _text(renderer.transcript)
    renderer.transcript.close()


def test_tools_are_compact_and_question_debug_is_hidden() -> None:
    renderer = StreamRenderer(Status())
    for index in range(3):
        call = {"tool_call_id": str(index), "tool_call_name": "view"}
        renderer.ingest("TOOL_CALL_START", call)
        renderer.ingest("TOOL_CALL_RESULT", {**call, "content": "done"})
    renderer.transcript.render(80)
    assert len(renderer.transcript.rows) == 3
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "private-id", "tool_call_name": "ask_user_question"})
    renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "private-id", "delta": '{"questions": []}'})
    renderer.ingest("TOOL_CALL_END", {"tool_call_id": "private-id"})
    assert "ask_user_question" not in _source(renderer) and "private-id" not in _source(renderer)
    renderer.transcript.close()


def test_notes_show_full_values_and_distinct_style_without_repeated_snapshots() -> None:
    from a13n_harness_ui.surfaces import NotePage, NoteView

    renderer = StreamRenderer(Status())
    page = NotePage(notes=(NoteView(key="design", value="details " * 30 + "THE END"),), total=1)
    renderer.restore_notes(page)
    renderer.restore_notes(page)
    assert len(renderer.transcript.blocks) == 1
    assert "THE END" in _source(renderer)
    renderer.transcript.render(60)
    assert "THE END" in _text(renderer.transcript)
    assert any(corner in _text(renderer.transcript) for corner in ("╭", "┌"))
    assert any(corner in _text(renderer.transcript) for corner in ("╰", "└"))
    assert any("bold" in style for row in renderer.transcript.rows for style, _ in row)
    assert next(iter(renderer.transcript.blocks.values())).kind == "notes"
    renderer.transcript.close()


def _unpadded_text(transcript: Transcript) -> str:
    return "\n".join(line.rstrip() for line in _text(transcript).split("\n"))


def test_adjacent_thinking_blocks_have_no_synthetic_blank_line() -> None:
    renderer = StreamRenderer(Status())
    for index, text in enumerate(("First thought.", "Second thought.", "Third thought.")):
        renderer.ingest("THINKING_TEXT_MESSAGE_CONTENT", {"message_id": str(index), "delta": text})
        renderer.ingest("THINKING_TEXT_MESSAGE_END", {"message_id": str(index)})
    renderer.transcript.render(80)
    assert _unpadded_text(renderer.transcript) == "First thought.\nSecond thought.\nThird thought.\n"
    assert len(renderer.transcript.blocks) == 3
    assert [block.source for block in renderer.transcript.blocks.values()] == [
        "First thought.",
        "Second thought.",
        "Third thought.",
    ]
    renderer.transcript.detailed = True
    renderer.transcript.dirty = True
    renderer.transcript.render(80)
    assert _unpadded_text(renderer.transcript) == "First thought.\nSecond thought.\nThird thought.\n"
    renderer.transcript.close()


def test_thinking_spacing_preserves_paragraphs_and_other_block_boundaries() -> None:
    transcript = Transcript()
    first = transcript.append("First paragraph.\n\nSecond paragraph.", markdown=True, kind="thinking")
    transcript.render(80)
    cached = transcript.blocks[first].rows
    transcript.append("Next thought.", markdown=True, kind="thinking")
    transcript.append("view · result", kind="tool")
    transcript.append("Final thought.", markdown=True, kind="thinking")
    transcript.append("Answer.", markdown=True)
    transcript.render(80)
    assert transcript.blocks[first].rows is cached
    assert _unpadded_text(transcript) == (
        "First paragraph.\n\nSecond paragraph.\nNext thought.\nview · result\nFinal thought.\n\nAnswer.\n"
    )
    assert transcript.locate((first, 2)) == 2
    transcript.close()


def test_thinking_adjacency_reflows_after_resize_and_eviction() -> None:
    transcript = Transcript(max_blocks=2)
    transcript.append("First thought.", markdown=True, kind="thinking")
    transcript.render(80)
    second = transcript.append("Second thought.", markdown=True, kind="thinking")
    transcript.render(8)
    assert "\n\n" not in _unpadded_text(transcript).rstrip("\n")
    transcript.render(80)
    assert _unpadded_text(transcript) == "First thought.\nSecond thought.\n"
    transcript.append("Answer.", markdown=True)
    transcript.render(80)
    assert transcript.evicted
    assert _unpadded_text(transcript) == "Second thought.\n\nAnswer.\n"
    assert transcript.anchor(0) == (second, 0)
    transcript.close()
