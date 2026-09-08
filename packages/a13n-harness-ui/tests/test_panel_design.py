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
    assert "more diff" in text and "edit-one" not in text
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
    assert "shell_exec · failed · exit 1 · pytest -q" in text
    assert "stdout  " not in text and "stderr  " not in text
    assert "stderr:" in text and "partial" in text and "more output" in text
    assert "line-2" in text and "line-19" not in text
    renderer.transcript.detailed = True
    renderer.transcript.dirty = True
    assert "line-19" in _text(renderer.transcript)
    renderer.transcript.close()


@pytest.mark.parametrize("phase, label", [("exited", "failed"), ("timed_out", "timed out"), ("cancelled", "cancelled")])
def test_long_command_cannot_hide_failure_in_clipped_panel_title(phase: str, label: str) -> None:
    from a13n_harness_ui.interactive.panels import shell_result_preview

    transcript = Transcript()
    preview = shell_result_preview(
        json.dumps({"status": {"phase": phase, "exit_code": 1}}), "pytest " + "long-path/" * 40, 3
    )
    assert preview is not None
    block = transcript.append("Details", kind="command")
    transcript.preview(block, preview)
    text = _text(transcript, 28)
    assert label in text
    assert len(text.splitlines()) == 1
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
