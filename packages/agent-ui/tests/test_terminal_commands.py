from __future__ import annotations

import sys
from pathlib import Path

import pytest
from a13n_ui.errors import AgentUiError
from a13n_ui.tui.commands import command_intent, match_slash_command
from a13n_ui.tui.editor import MAX_EDITOR_BYTES, edit_text, resolve_editor_command
from a13n_ui.tui.intents import OpenExternalEditor, OpenOverlay, OpenWorkbench, StartNewDraft
from a13n_ui.tui.models import TerminalLifecycle, TerminalMode, TerminalState, WorkbenchState


def test_slash_registry_matches_only_exact_known_commands() -> None:
    command = match_slash_command("  /workbench  ")

    assert command is not None
    assert command.name == "workbench"
    assert match_slash_command("/workbench now") is None
    assert match_slash_command("/not-a-command") is None
    assert isinstance(command_intent("workbench", TerminalState()), OpenWorkbench)


def test_editor_resolution_prefers_visual_without_using_a_shell() -> None:
    assert resolve_editor_command({"VISUAL": "code --wait", "EDITOR": "vim"}) == ("code", "--wait")
    assert resolve_editor_command({"EDITOR": "vim -f"}) == ("vim", "-f")

    with pytest.raises(AgentUiError, match="VISUAL") as exc_info:
        resolve_editor_command({})
    assert exc_info.value.code == "editor_not_configured"


@pytest.mark.anyio
async def test_external_editor_round_trip_is_async_bounded_and_utf8() -> None:
    script = (
        "from pathlib import Path; import sys; "
        "path = Path(sys.argv[1]); path.write_text(path.read_text() + ' edited', encoding='utf-8')"
    )

    result = await edit_text((sys.executable, "-c", script), "draft")

    assert result == "draft edited"


@pytest.mark.anyio
async def test_external_editor_rejects_oversized_draft_before_launch() -> None:
    with pytest.raises(AgentUiError) as exc_info:
        await edit_text(("unused-editor",), "x" * (MAX_EDITOR_BYTES + 1))

    assert exc_info.value.code == "editor_draft_too_large"


def test_editor_command_is_available_only_after_terminal_startup() -> None:
    state = TerminalState(lifecycle=TerminalLifecycle.STARTING)

    assert command_intent("editor", state) is None


def test_workbench_commands_target_selected_thread_not_previous_focus() -> None:
    state = TerminalState(
        lifecycle=TerminalLifecycle.READY,
        mode=TerminalMode.WORKBENCH,
        focused_thread_id="thread-previous",
        previous_focused_thread_id="thread-previous",
        workbench=WorkbenchState(selected_thread_id="thread-selected"),
    )

    editor = command_intent("editor", state)
    agent = command_intent("agent", state)
    skills = command_intent("skills", state)

    assert isinstance(editor, OpenExternalEditor)
    assert editor.key == "thread-selected"
    assert isinstance(agent, OpenOverlay)
    assert agent.context_key == "thread-selected"
    assert isinstance(skills, OpenOverlay)
    assert skills.context_key == "thread-selected"


def test_new_command_uses_launch_project_independent_of_workbench_filter() -> None:
    state = TerminalState(
        launch_project_id="project-launch",
        project_filter_id="project-other",
    )

    intent = command_intent("new", state)

    assert isinstance(intent, StartNewDraft)
    assert intent.defaults is not None
    assert intent.defaults.project_id == "project-launch"


@pytest.mark.anyio
async def test_external_editor_nonzero_exit_preserves_error_and_cleans_temp_file(tmp_path: Path) -> None:
    marker = tmp_path / "editor-path"
    script = (
        "from pathlib import Path; import sys; "
        "Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8'); raise SystemExit(3)"
    )

    with pytest.raises(AgentUiError) as exc_info:
        await edit_text((sys.executable, "-c", script, str(marker)), "draft")

    assert exc_info.value.code == "editor_process_failed"
    assert not Path(marker.read_text(encoding="utf-8")).exists()


@pytest.mark.anyio
async def test_external_editor_rejects_oversized_result_and_cleans_temp_file(tmp_path: Path) -> None:
    marker = tmp_path / "editor-path"
    script = (
        "from pathlib import Path; import sys; "
        "path = Path(sys.argv[2]); Path(sys.argv[1]).write_text(str(path), encoding='utf-8'); "
        f"path.write_bytes(b'x' * {MAX_EDITOR_BYTES + 1})"
    )

    with pytest.raises(AgentUiError) as exc_info:
        await edit_text((sys.executable, "-c", script, str(marker)), "draft")

    assert exc_info.value.code == "editor_result_too_large"
    assert not Path(marker.read_text(encoding="utf-8")).exists()
