"""Shared panel hierarchy, literal output, and explicit activity provenance."""

from __future__ import annotations

import json
from functools import partial

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.interactive.transcript import Transcript
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth

from .terminal_display_fixtures import present_native_result, present_text, present_tool


def _text(transcript: Transcript, width: int = 80) -> str:
    transcript.render(width)
    return "\n".join("".join(text for _, text in row).rstrip() for row in transcript.rows)


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


def test_edit_applied_replaces_pending_call_and_retains_full_diff_on_expand() -> None:
    renderer = StreamRenderer(Status())
    present_tool(renderer, "edit-one", name="edit", arguments="", arguments_complete=False, status="pending")
    present_tool(renderer, "edit-one", arguments='{"file_path":"file.py"}')
    present_tool(renderer, "edit-one", arguments_complete=True)
    present_tool(
        renderer,
        "edit-one",
        metadata={
            "a13n.harness-ui.applied_edit": {
                "file_path": "file.py",
                "tool_call_id": "edit-one",
                "before": "old\n" * 20,
                "after": "new\n" * 20,
            }
        },
    )
    present_tool(renderer, "edit-one", result='{"ok":true}')
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
    present_text(
        renderer,
        "Background process process-1 has exited.",
        message_id="input-one",
        kind="input" if "user" == "user" else "text",
        metadata={"a13n.steering-source": source} if source else {},
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
        present_tool(renderer, "one", name="view", arguments="", arguments_complete=False, status="pending")
        assert _text(renderer.transcript) == "Read path unavailable …"
        present_tool(renderer, "one", arguments='{"file_path":"file.py"}')
        present_tool(renderer, "one", arguments_complete=True)
        present_tool(renderer, "one", result=result)
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
            present_tool(renderer, "one", name=name, arguments="", arguments_complete=False, status="pending")
            present_tool(renderer, "one", arguments=json.dumps(arguments))
            present_tool(renderer, "one", arguments_complete=True)
        present_native_result(
            renderer,
            part,
        )
        assert len(renderer.transcript.blocks) == 1
        text = _text(renderer.transcript)
        assert text.startswith(f"{'Run' if name == 'shell_exec' else 'Call'} {state}:") and len(text.splitlines()) == 1
        assert "{" not in text and "extra_forbidden" not in text and "native result/retry" not in text
        assert len(renderer._tools) <= 128 and renderer.status.state == "working"
        renderer.transcript.detailed = True
        renderer.transcript.dirty = True
        assert "extra_forbidden" in _text(renderer.transcript)
        block = next(iter(renderer.transcript.blocks.values()))
        assert "extra_forbidden" in block.source and "Extra inputs are not permitted" in block.source
        if with_start:
            assert json.dumps(arguments, ensure_ascii=False, indent=2) in block.source
    finally:
        renderer.transcript.close()


@pytest.mark.parametrize("mode", ["concise", "detailed"])
def test_native_retries_obey_child_visibility_and_run_scoped_correlation(mode) -> None:
    renderer = StreamRenderer(Status(mode=mode))
    try:
        for run in ("root", "child"):
            present_tool(
                renderer,
                "same",
                run_id=run,
                child=run == "child",
                name="task_create",
                arguments="",
                arguments_complete=False,
                status="pending",
            )
        renderer.status.state = "cancelling"
        present_native_result(
            renderer,
            {
                "tool_call_id": "same",
                "tool_name": "task_create",
                "part_kind": "retry-prompt",
                "content": "child retry details",
            },
            run_id="child",
            child=True,
        )
        assert len(renderer._tools) <= 128
        assert renderer.status.state == "cancelling"
        text = _text(renderer.transcript)
        if mode == "detailed":
            assert "Call retry:" in text and " · child" in text
        else:
            assert "retry" not in text
        assert len(renderer.transcript.blocks) == (2 if mode == "detailed" else 1)
    finally:
        renderer.transcript.close()
