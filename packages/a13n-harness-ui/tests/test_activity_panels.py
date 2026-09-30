"""Task and process inspection keep semantic contrast without extra chrome."""

from __future__ import annotations

import json

import pytest
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.interactive.rendering import Status, StreamRenderer
from a13n_harness_ui.interactive.shell import CliShell
from a13n_harness_ui.interactive.theme import activity_colors, prompt_toolkit_style_rules, resolve_theme
from a13n_harness_ui.interactive.transcript import Transcript
from a13n_harness_ui.surfaces import TaskPage, TaskView
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth


class SizedOutput(DummyOutput):
    def __init__(self, width: int) -> None:
        self.width = width

    def get_size(self) -> Size:
        return Size(rows=24, columns=self.width)


@pytest.mark.parametrize("width", [10, 20, 40, 80])
def test_task_heading_retains_toggle_and_rows_are_width_safe(width: int) -> None:
    with create_pipe_input(), create_app_session(output=SizedOutput(width)):
        shell = CliShell(CliRequest())
        try:
            shell.renderer.tasks.restore(
                TaskPage(
                    tasks=(
                        TaskView(task_id="task-1", version=1, subject="Read 界" * 30, status="in_progress"),
                        TaskView(
                            task_id="task-2", version=1, subject="Check", status="pending", blocked_by=("task-1",)
                        ),
                        TaskView(task_id="task-3", version=1, subject="Wait", status="pending"),
                        TaskView(task_id="task-4", version=1, subject="Done", status="completed"),
                    )
                )
            )
            fragments = shell._task_text()
            lines = "".join(fragment[1] for fragment in fragments).splitlines()
            assert len(lines) == 5
            assert lines[0].startswith("Tasks") and "F2" in lines[0]
            assert all(get_cwidth(line) <= width for line in lines)
            assert lines[1].endswith("…")
            assert any("task-pane.heading" in style for style, *_ in fragments)
            for state in ("running", "waiting", "completed"):
                assert any(f"activity.{state}" in style for style, *_ in fragments)
            shell.renderer.tasks.expanded = False
            collapsed = "".join(fragment[1] for fragment in shell._task_text()).splitlines()
            assert len(collapsed) == 1 and "F2" in collapsed[0]
        finally:
            shell.renderer.transcript.close()


@pytest.mark.parametrize("preference", ["auto", "dark", "light"])
def test_activity_styles_preserve_passive_palette_and_explicit_theme_contrast(preference) -> None:
    theme = resolve_theme(preference, environ={})
    colors = activity_colors(theme)
    rules = prompt_toolkit_style_rules(theme)
    assert len(set(colors.values())) == len(colors)
    style = Style.from_dict(rules)
    assert style.get_attrs_for_style_str("class:task-pane.heading").bold
    for state in ("running", "waiting", "completed", "failed"):
        assert style.get_attrs_for_style_str(f"class:activity.{state}").color
    if preference == "auto":
        assert rules[""] == "bg:default fg:default"
        assert all("#" not in value for value in colors.values())
        assert "bg:" not in rules["task-pane.heading"]
    else:
        assert "bg:" in rules["task-pane.heading"]
        assert all(value.startswith("#") for value in colors.values())


@pytest.mark.parametrize("preference", ["auto", "dark", "light"])
@pytest.mark.parametrize("width", [20, 40, 80, 140])
def test_process_panel_emphasizes_status_before_command_and_retains_details(preference, width: int) -> None:
    transcript = Transcript()
    transcript.theme = resolve_theme(preference, environ={})
    source = (
        "Background processes · last observed\n"
        "process-one · running · pytest [red]界[/red] · Run root\n"
        "process-two · unavailable · sleep 10 · Run child\n"
        "process-three · exited · echo done · Run root\n"
        "process-four · failed · false · Run root\n"
        "Live output incomplete · status may be stale.\n"
        "Ctrl+O · tool output"
    )
    try:
        transcript.append(source, kind="processes")
        transcript.render(width)
        lines = ["".join(text for _, text in row) for row in transcript.rows]
        text = "\n".join(lines)
        compact = "".join(text.split()).replace("│", "")
        assert all(get_cwidth(line) <= width for line in lines)
        for value in ("[running]", "[unavailable]", "[exited]", "[failed]", "process-one", "Runchild"):
            assert value in compact
        assert "[red]界[/red]" in compact  # Command markup remains literal.
        assert "incomplete" in compact
        assert "Ctrl+O" in compact
        if width >= 80:
            assert "lastobserved" in compact
        if width >= 80:
            assert "[running] pytest [red]界[/red] · process-one · Run root" in text
        colors = activity_colors(transcript.theme)
        for state, label in (
            ("running", "[running]"),
            ("waiting", "[unavailable]"),
            ("completed", "[exited]"),
            ("failed", "[failed]"),
        ):
            expected = colors[state]
            expected = expected if expected.startswith("#") else "ansi" + expected.replace("_", "")
            styled = "".join(part for row in transcript.rows for style, part in row if f"fg:{expected}" in style)
            assert label in "".join(styled.split())
        assert len(transcript.blocks) == 1
        if width == 140:
            assert len(lines) == 8  # Same border and body rows as the plain snapshot panel.
    finally:
        transcript.close()


@pytest.mark.anyio
async def test_ps_command_uses_styled_snapshot_without_a_live_backend() -> None:
    with create_pipe_input(), create_app_session(output=DummyOutput()):
        shell = CliShell(CliRequest())
        try:
            invocation = shell.registry.parse("/ps")
            assert invocation is not None
            await shell.command(invocation)
            blocks = list(shell.renderer.transcript.blocks.values())
            assert len(blocks) == 1 and blocks[0].kind == "processes"
            shell.renderer.transcript.render(100)
            text = "\n".join("".join(part[1] for part in row) for row in shell.renderer.transcript.rows)
            assert "Background processes" in text
            assert "No background processes observed" in text
        finally:
            shell.renderer.transcript.close()


def test_process_panel_recolors_when_auto_and_explicit_theme_share_the_variant() -> None:
    transcript = Transcript()
    try:
        transcript.theme = resolve_theme("auto", environ={})
        transcript.append("Background processes\nprocess-one · running · sleep 1 · Run root", kind="processes")
        transcript.render(80)
        assert any("ansicyan" in style for row in transcript.rows for style, _ in row)
        transcript.theme = resolve_theme("dark")
        transcript.dirty = True
        transcript.render(80)
        assert not any("ansicyan" in style for row in transcript.rows for style, _ in row)
        assert any("#76d4c4" in style for row in transcript.rows for style, _ in row)
    finally:
        transcript.close()


@pytest.mark.parametrize("callback", [False, True])
def test_nonzero_process_exit_is_failed_not_a_green_completion(callback: bool) -> None:
    renderer = StreamRenderer(Status())
    try:
        running = json.dumps({"process_id": "process-one", "status": {"phase": "running"}})
        renderer._observe_shell_result(running, "pytest", "run-one")
        if callback:
            renderer.ingest_control(
                "CUSTOM",
                {
                    "name": "a13n.shell.status",
                    "value": {"event": {"process_id": "process-one", "phase": "exited", "exit_code": 127}},
                },
                run_id="run-one",
                child=True,
            )
        else:
            renderer._observe_shell_result(
                json.dumps({"process_id": "process-one", "status": {"phase": "exited", "exit_code": 127}}),
                "pytest",
                "run-one",
            )
        renderer._observe_shell_result(running, "pytest", "run-one")
        assert renderer.background_hint == ""
        snapshot = renderer.process_details()
        assert "failed (exit 127)" in snapshot
        renderer.transcript.append(snapshot, kind="processes")
        renderer.transcript.theme = resolve_theme("auto", environ={})
        renderer.transcript.render(100)
        red = "".join(part for row in renderer.transcript.rows for style, part in row if "ansired" in style)
        assert "[failed (exit 127)]" in red
    finally:
        renderer.transcript.close()


def test_context_row_removal_rereads_surviving_summary_without_stale_details() -> None:
    from a13n_stream_protocol.display import BlocksRemove

    from .terminal_display_fixtures import present_context, present_summary, state_for

    renderer = StreamRenderer(Status())
    present_summary(renderer, "compact-one", "retained summary")
    present_context(renderer, "compact-one", status="succeeded")
    state = state_for(renderer)
    assert any("retained summary" in block.source for block in renderer.transcript.blocks.values())
    removed = ("root:execution:compact-one",)
    state.publish([BlocksRemove(ids=removed, omitted=1)])
    changed = renderer.remove_blocks(state, removed)
    renderer.display_blocks(state, changed)
    sources = "\n".join(block.source for block in renderer.transcript.blocks.values())
    assert "Compacting context" in sources
    assert "retained summary" not in sources
    assert "Context lifecycle" not in sources
    removed = ("root:context:compact-one",)
    state.publish([BlocksRemove(ids=removed, omitted=2)])
    renderer.display_blocks(state, renderer.remove_blocks(state, removed))
    assert not renderer.transcript.blocks


def test_full_tool_arguments_use_transcript_budget_not_pending_output_budget() -> None:
    from .terminal_display_fixtures import present_tool

    renderer = StreamRenderer(Status())
    arguments = json.dumps({"value": "x" * 12000 + "full-arguments-tail"})
    present_tool(renderer, "large", name="custom_tool", arguments=arguments, arguments_complete=True)
    assert any("full-arguments-tail" in block.source for block in renderer.transcript.blocks.values())
    assert all(not hasattr(preview, "arguments") for preview in renderer._tools.values())
