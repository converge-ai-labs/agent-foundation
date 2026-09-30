"""Shared panel hierarchy, literal output, and explicit activity provenance."""

from __future__ import annotations

import json
from functools import partial

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.interactive.theme import resolve_theme
from a13n_harness_ui.interactive.transcript import Transcript
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth


def _text(transcript: Transcript, width: int = 80) -> str:
    transcript.render(width)
    return "\n".join("".join(text for _, text in row).rstrip() for row in transcript.rows)


@pytest.mark.parametrize("legacy_windows", [False, True])
@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("width", [28, 80])
def test_custom_panels_share_frame_and_preserve_literal_output(
    theme: str, width: int, legacy_windows: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_harness_ui.interactive.transcript as module

    monkeypatch.setattr(module, "Console", partial(module.Console, legacy_windows=legacy_windows))
    transcript = Transcript()
    transcript.theme = resolve_theme(theme)
    for kind in ("command", "edit", "info", "notes", "summary", "compact"):
        transcript.append("Title\n[bold]literal[/bold]\n+added\n-removed", kind=kind)
    text = _text(transcript, width)
    assert text.count("╭") + text.count("┌") == 6
    assert text.count("╰") + text.count("└") == 6
    assert "[bold]literal[/bold]" in text
    assert all(get_cwidth(line) <= width for line in text.splitlines())
    assert any("ansigreen" in style for row in transcript.rows for style, _ in row)
    assert any("ansired" in style for row in transcript.rows for style, _ in row)
    transcript.close()


@pytest.mark.parametrize("terminal", ["dumb", "unknown", "xterm-256color"])
@pytest.mark.parametrize("legacy_windows", [False, True])
def test_transcript_layout_uses_viewport_width_in_any_terminal_environment(
    terminal: str, legacy_windows: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import a13n_harness_ui.interactive.transcript as module

    monkeypatch.setenv("TERM", terminal)
    monkeypatch.setenv("COLUMNS", "120")
    monkeypatch.delenv("LINES", raising=False)
    monkeypatch.setattr(module, "Console", partial(module.Console, legacy_windows=legacy_windows))
    transcript = Transcript()
    try:
        transcript.append("Review\n" + "wrapped output " * 12, kind="command")
        narrow = _text(transcript, 28)
        wide = _text(transcript, 80)
        assert all(get_cwidth(line) <= 28 for line in narrow.splitlines())
        assert len(narrow.splitlines()) > len(wide.splitlines())
        assert narrow.count("wrapped") == narrow.count("output") == 12
        assert all(get_cwidth(line) <= 80 for line in wide.splitlines())
    finally:
        transcript.close()


@pytest.mark.parametrize("detailed", [False, True])
@pytest.mark.parametrize("kind", ["tool", "command", "edit"])
def test_thinking_touches_tool_frame_but_answer_keeps_paragraph_spacing(kind: str, detailed: bool) -> None:
    transcript = Transcript()
    transcript.detailed = detailed
    transcript.append("Thinking.", markdown=True, kind="thinking")
    transcript.append("Tool\noutput", kind=kind)
    text = _text(transcript)
    lines = text.splitlines()
    assert lines[0] == "Thinking."
    assert lines[1].strip()
    transcript.close()


def test_edit_applied_replaces_pending_call_and_retains_full_diff_on_expand() -> None:
    renderer = StreamRenderer(Status())
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "edit-one", "tool_call_name": "edit"})
    renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "edit-one", "delta": '{"file_path":"file.py"}'})
    renderer.ingest("TOOL_CALL_END", {"tool_call_id": "edit-one"})
    renderer.ingest(
        "CUSTOM",
        {
            "name": "a13n.filesystem.edit_applied",
            "value": {
                "event": {
                    "file_path": "file.py",
                    "tool_call_id": "edit-one",
                    "before": "old\n" * 20,
                    "after": "new\n" * 20,
                }
            },
        },
    )
    renderer.ingest("TOOL_CALL_RESULT", {"tool_call_id": "edit-one", "content": '{"ok":true}'})
    assert len(renderer.transcript.blocks) == 1
    text = _text(renderer.transcript)
    assert "Edit · file.py · +20 -20" in text
    assert "more diff" not in text and "edit-one" not in text
    assert text.count("+new") == 20 and text.count("-old") == 20
    renderer.transcript.detailed = True
    renderer.transcript.dirty = True
    expanded = _text(renderer.transcript)
    assert expanded.count("+new") == 20
    assert "Tool result · edit-one" in expanded and '"ok": true' in expanded
    renderer.transcript.close()


def test_shell_result_has_no_stdout_prefix_and_keeps_coverage_and_details() -> None:
    renderer = StreamRenderer(Status())
    renderer.ingest("TOOL_CALL_START", {"tool_call_id": "one", "tool_call_name": "shell_exec"})
    renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "one", "delta": '{"command":"pytest -q"}'})
    renderer.ingest("TOOL_CALL_END", {"tool_call_id": "one"})
    renderer.ingest(
        "TOOL_CALL_RESULT",
        {
            "tool_call_id": "one",
            "content": json.dumps(
                {
                    "ok": True,
                    "status": {"phase": "exited", "exit_code": 1},
                    "stdout": {"text": "\n".join(f"line-{i}" for i in range(20)), "coverage": "partial"},
                    "stderr": {"text": "failure", "coverage": "complete"},
                }
            ),
        },
    )
    text = _text(renderer.transcript)
    assert text == "Run failed · exit 1 · output partial · pytest -q"
    assert "line-2" not in text and "line-19" not in text
    renderer.transcript.detailed = True
    renderer.transcript.dirty = True
    assert "line-19" in _text(renderer.transcript)
    renderer.transcript.close()


@pytest.mark.parametrize("phase, label", [("exited", "failed"), ("timed_out", "timed out"), ("cancelled", "cancelled")])
def test_long_command_wraps_without_hiding_failure(phase: str, label: str) -> None:
    from a13n_harness_ui.interactive.panels import shell_result_preview

    transcript = Transcript()
    preview = shell_result_preview(
        json.dumps({"status": {"phase": phase, "exit_code": 1}}), "pytest " + "long-path/" * 40
    )
    assert preview is not None
    block = transcript.append("Details", kind="command")
    transcript.preview(block, preview)
    text = _text(transcript, 28)
    assert label in text
    assert len(text.splitlines()) > 1
    assert text.replace("\n", "").count("long-path/") == 40
    assert "exit 1" in text
    transcript.close()


def test_long_edit_preview_discloses_character_omission_and_preserves_closed_frame() -> None:
    transcript = Transcript()
    source = "Edit · file.py\n-" + "old" * 600 + "\n+" + "new" * 600
    block = transcript.append(source, kind="edit")
    transcript.preview(block, source)
    text = _text(transcript, 50)
    assert "preview shortened" in text
    assert text.splitlines()[-1].startswith(("╰", "└"))
    transcript.detailed = True
    transcript.dirty = True
    assert "+new" in _text(transcript, 50)
    transcript.close()


@pytest.mark.parametrize("source", ["background_process", "async_subagent", None])
def test_activity_input_uses_provenance_not_message_text(source: str | None) -> None:
    renderer = StreamRenderer(Status())
    renderer.ingest(
        "TEXT_MESSAGE_CONTENT",
        {
            "role": "user",
            "message_id": "input-one",
            "delta": "Background process process-1 has exited.",
            "metadata": {"a13n.steering-source": source} if source else {},
        },
    )
    block = next(iter(renderer.transcript.blocks.values()))
    assert block.kind == ("tool" if source else "user")
    assert block.source.startswith("Activity · " if source else "> ")
    renderer.transcript.close()


@pytest.mark.anyio
async def test_activity_region_is_separated_above_status_and_composer() -> None:
    import asyncio

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.renderer._observe_shell_result(
            json.dumps({"process_id": "process-one", "status": {"phase": "running"}}), "sleep 10", "run-one"
        )
        terminal = asyncio.create_task(shell.app.run_async())
        try:
            for _ in range(100):
                screen = shell.app.renderer.last_rendered_screen
                if screen is not None:
                    break
                await asyncio.sleep(0.01)
            assert screen is not None
            lines = ["".join(row[x].char for x in range(80)) for _, row in sorted(screen.data_buffer.items())]
            index = next(i for i, line in enumerate(lines) if "Background 1" in line)
            assert lines[index - 1] == "─" * 80
            assert "ctx" in lines[index + 1]
            assert not lines[index].lstrip().startswith(">")
        finally:
            shell.app.exit()
            await terminal
            shell.renderer.transcript.close()


@pytest.mark.anyio
async def test_status_is_one_structured_panel_without_duplicate_usage() -> None:
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        shell.status.model = "test:model"
        await shell.command(shell.registry.parse("/status"))
        block = next(iter(shell.renderer.transcript.blocks.values()))
        assert block.kind == "info"
        text = _text(shell.renderer.transcript)
        assert "Status ·" in text and "Reasoning" in text and "Workspace" in text
        assert "Root Run usage" not in text and "unknown / unknown" in text
        assert "Context =" not in text and "cumulative usage" not in text
        assert "recorded totals" not in text
        assert any(corner in text for corner in ("╭", "┌"))
        assert any(corner in text for corner in ("╰", "└"))
        shell.renderer.transcript.close()


@pytest.mark.parametrize(
    "state, tone",
    [
        ("running", "running"),
        ("waiting", "waiting"),
        ("retry", "waiting"),
        ("denied", "muted"),
        ("completed", "completed"),
        ("exit 0", "completed"),
        ("failed", "muted"),
        ("timed out", "muted"),
        ("cancelled", "muted"),
        ("returned", "muted"),
        ("status unavailable", "muted"),
    ],
)
def test_tool_row_maps_status_tones_and_leaves_payload_literal(state, tone) -> None:
    from a13n_harness_ui.interactive.theme import activity_colors
    from a13n_harness_ui.interactive.transcript import _tool_row

    theme = resolve_theme("dark")
    colors = activity_colors(theme)
    payload = "cat [bold]file[/bold] | grep 'failed · result'"
    row = _tool_row(f"tool_name | {state} | {payload}", theme)
    assert row.plain == f"tool_name | {state} | {payload}"
    status_span = next(span for span in row.spans if row.plain[span.start : span.end] == state)
    payload_span = next(span for span in row.spans if row.plain[span.start : span.end] == payload)
    assert status_span.style == colors[tone]
    assert payload_span.style == colors["muted"]
    assert all("bold" not in str(style).split() for style in [row.style, *(span.style for span in row.spans)])


@pytest.mark.parametrize("theme", ["auto", "dark", "light"])
@pytest.mark.parametrize(
    ("kind", "state", "tone"), [("tool", "failed", "muted"), ("command", "completed", "completed")]
)
def test_transcript_renders_status_colors_and_literal_payloads(theme, kind, state, tone) -> None:
    from a13n_harness_ui.interactive.theme import activity_colors

    transcript = Transcript()
    transcript.theme = resolve_theme(theme)
    payload = "cat [bold]file[/bold] | grep 'failed · result'"
    block = transcript.append("Expanded details", kind=kind)
    transcript.preview(block, f"tool_name | {state} | {payload}")
    try:
        assert _text(transcript, 120) == f"tool_name | {state} | {payload}"
        fragments = transcript.rows[0]
        colors = activity_colors(transcript.theme)
        expected = colors[tone]
        expected = expected if expected.startswith("#") else "ansi" + expected.replace("_", "")
        assert any(text == state and f"fg:{expected}" in style for style, text in fragments)
        muted = colors["muted"]
        muted = muted if muted.startswith("#") else "ansi" + muted.replace("_", "")
        assert any(payload in text and f"fg:{muted}" in style for style, text in fragments)
        assert all("bold" not in style.split() for style, _ in fragments)
    finally:
        transcript.close()


@pytest.mark.parametrize("theme", ["auto", "dark", "light"])
@pytest.mark.parametrize(
    "preview",
    [
        "Run failed · exit 1 · python3 -u -c 'print(1)'",
        "Run timed out · sleep 120",
        "Call mkdir failed: permission denied",
        "Read file.py denied: environment_denied",
        "Explored 2 files\n  Read file.py failed: not found",
    ],
)
def test_semantic_tool_failures_keep_text_without_error_emphasis(theme: str, preview: str) -> None:
    transcript = Transcript()
    transcript.theme = resolve_theme(theme)
    block = transcript.append("Expanded details", kind="tool")
    transcript.preview(block, preview, lines=len(preview.splitlines()))
    try:
        assert _text(transcript, 120) == preview
        assert all(
            "ansired" not in style and "bold" not in style.split() for row in transcript.rows for style, _ in row
        )
    finally:
        transcript.close()


@pytest.mark.parametrize(
    "result, state",
    [
        ('{"ok":true,"content":"output-marker"}', "completed"),
        ('{"ok":false,"error":"output-marker"}', "failed"),
        ('{"content":"output-marker"}', "returned"),
        ('{"ok":1,"content":"output-marker"}', "returned"),
        ("output-marker", "returned"),
    ],
)
def test_ordinary_tool_rows_keep_output_in_details_and_only_report_observed_success(result, state) -> None:
    renderer = StreamRenderer(Status())
    try:
        renderer.ingest("TOOL_CALL_START", {"tool_call_id": "one", "tool_call_name": "view"})
        assert _text(renderer.transcript) == "Read path unavailable …"
        renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "one", "delta": '{"file_path":"file.py"}'})
        renderer.ingest("TOOL_CALL_END", {"tool_call_id": "one"})
        renderer.ingest("TOOL_CALL_RESULT", {"tool_call_id": "one", "content": result})
        text = _text(renderer.transcript)
        assert text.startswith("Read failed:") if state == "failed" else text == "Read file.py"
        assert "{" not in text and "output-marker" not in text
        assert len(renderer.transcript.blocks) == 1
        renderer.transcript.detailed = True
        renderer.transcript.dirty = True
        expanded = _text(renderer.transcript)
        assert "output-marker" in expanded and '"file_path": "file.py"' in expanded
    finally:
        renderer.transcript.close()


@pytest.mark.parametrize("theme", ["auto", "dark", "light"])
def test_expanded_shell_title_keeps_normal_weight(theme) -> None:
    transcript = Transcript()
    transcript.theme = resolve_theme(theme)
    transcript.append("shell_exec | returned\n[bold]literal output[/bold]", kind="command")
    try:
        text = _text(transcript)
        assert "shell_exec | returned" in text and "[bold]literal output[/bold]" in text
        assert all("bold" not in style.split() for row in transcript.rows for style, _ in row)
    finally:
        transcript.close()


@pytest.mark.parametrize("name", ["task_create", "shell_exec", "ask_user_question"])
@pytest.mark.parametrize("with_start", [False, True])
@pytest.mark.parametrize("state", ["retry", "failed", "denied"])
def test_native_tool_outcomes_use_the_correlated_row_and_keep_details(name, with_start, state) -> None:
    renderer = StreamRenderer(Status())
    arguments = {"command": "echo test"} if name == "shell_exec" else {"subject": "Test task"}
    part = {
        "tool_name": name,
        "tool_call_id": "one",
        "part_kind": "retry-prompt" if state == "retry" else "tool-return",
        "content": [{"type": "extra_forbidden", "loc": ["status"], "msg": "Extra inputs are not permitted"}],
        **({"outcome": state} if state != "retry" else {}),
    }
    try:
        if with_start:
            renderer.ingest("TOOL_CALL_START", {"tool_call_id": "one", "tool_call_name": name})
            renderer.ingest("TOOL_CALL_ARGS", {"tool_call_id": "one", "delta": json.dumps(arguments)})
            renderer.ingest("TOOL_CALL_END", {"tool_call_id": "one"})
        renderer.ingest(
            "CUSTOM",
            {"name": "a13n.pydantic_ai.function_tool_result", "value": {"event": {"part": part}}},
        )
        assert len(renderer.transcript.blocks) == 1
        text = _text(renderer.transcript)
        assert text.startswith(f"{'Run' if name == 'shell_exec' else 'Call'} {state}:") and len(text.splitlines()) == 1
        assert "{" not in text and "extra_forbidden" not in text and "native result/retry" not in text
        assert not renderer._tools and renderer.status.state == "working"
        renderer.transcript.detailed = True
        renderer.transcript.dirty = True
        assert "extra_forbidden" in _text(renderer.transcript)
        block = next(iter(renderer.transcript.blocks.values()))
        assert json.dumps(part, ensure_ascii=False, indent=2) in block.source
        if with_start:
            assert json.dumps(arguments, ensure_ascii=False, indent=2) in block.source
    finally:
        renderer.transcript.close()


@pytest.mark.parametrize("mode", ["concise", "detailed"])
def test_native_retries_obey_child_visibility_and_run_scoped_correlation(mode) -> None:
    renderer = StreamRenderer(Status(mode=mode))
    try:
        for run in ("root", "child"):
            renderer.ingest(
                "TOOL_CALL_START",
                {"tool_call_id": "same", "tool_call_name": "task_create"},
                run_id=run,
                child=run == "child",
            )
        renderer.status.state = "cancelling"
        renderer.ingest(
            "CUSTOM",
            {
                "name": "a13n.pydantic_ai.function_tool_result",
                "value": {
                    "event": {
                        "part": {
                            "tool_call_id": "same",
                            "tool_name": "task_create",
                            "part_kind": "retry-prompt",
                            "content": "child retry details",
                        }
                    }
                },
            },
            run_id="child",
            child=True,
        )
        assert ("root", "same") in renderer._tools and ("child", "same") not in renderer._tools
        assert renderer.status.state == "cancelling"
        text = _text(renderer.transcript)
        if mode == "detailed":
            assert "Call retry:" in text and " · child" in text
        else:
            assert "retry" not in text
        assert len(renderer.transcript.blocks) == (2 if mode == "detailed" else 1)
    finally:
        renderer.transcript.close()


@pytest.mark.parametrize("theme", ["auto", "dark", "light"])
@pytest.mark.parametrize(
    "preview",
    [
        "Read packages/a13n-harness-ui/tests/test_path_display.py",
        "Find **/*.py in packages/a13n-harness-ui",
        "Search literal [bold]query[/bold] in packages",
        "List packages/a13n-harness-ui/tests",
        "Run exit 0 · python -m pytest packages/a13n-harness-ui/tests",
        "Call mkdir packages/a13n-harness-ui/tests",
        "Delegate explorer · Inspect the terminal rendering implementation",
        "Steer child-one · Check the long-path presentation",
        "Modified: packages/a13n-harness-ui/tests/test_path_display.py",
        "Explored 2 files\n  Read packages/first.py\n  Read packages/second.py",
    ],
)
def test_entire_tool_summary_and_wrapped_continuations_are_subdued(theme: str, preview: str) -> None:
    from a13n_harness_ui.interactive.theme import activity_colors

    transcript = Transcript()
    transcript.theme = resolve_theme(theme)
    block = transcript.append("Expanded details", kind="tool")
    transcript.preview(block, preview)
    try:
        colors = activity_colors(transcript.theme)
        muted = colors["muted"]
        muted = muted if muted.startswith("#") else "ansi" + muted.replace("_", "")
        for width in (28, 120, 28):
            _text(transcript, width)
            assert all(
                f"fg:{muted}" in style and "bold" not in style.split() and "dim" not in style.split()
                for row in transcript.rows
                for style, text in row
                if text.strip()
            )
        transcript.append("Assistant prose", markdown=True)
        _text(transcript)
        assert any(
            text.rstrip() == "Assistant prose" and f"fg:{muted}" not in style
            for row in transcript.rows
            for style, text in row
        )
    finally:
        transcript.close()
