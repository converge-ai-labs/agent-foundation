"""Single static registry shared by slash commands and the command palette."""

from __future__ import annotations

from dataclasses import dataclass

from a13n_ui.tui.intents import (
    ArchiveThread,
    CancelFocusedOperation,
    ExitTerminal,
    OpenExternalEditor,
    OpenOverlay,
    StartNewDraft,
    TerminalIntent,
    ToggleReasoning,
    ToggleToolDetails,
)
from a13n_ui.tui.models import ControlMode, TerminalLifecycle, TerminalState, ThreadViewState


@dataclass(frozen=True, slots=True)
class TerminalCommand:
    name: str
    description: str

    @property
    def slash(self) -> str:
        return f"/{self.name}"


COMMANDS = (
    TerminalCommand("new", "Start a new root Thread draft"),
    TerminalCommand("threads", "Find and open a Thread"),
    TerminalCommand("skills", "Browse effective Skills for this input"),
    TerminalCommand("status", "Show current Thread and App status"),
    TerminalCommand("setup", "Set up accounts, editable Agents, and default execution authority"),
    TerminalCommand("details", "Toggle expanded tool details"),
    TerminalCommand("thinking", "Toggle permitted reasoning content"),
    TerminalCommand("agent", "Select the Agent for this Thread"),
    TerminalCommand("environment", "Select the Environment profile"),
    TerminalCommand("extensions", "Select Plugins, Run Extensions, and MCP servers"),
    TerminalCommand("editor", "Edit the current draft in $VISUAL or $EDITOR"),
    TerminalCommand("cancel", "Cancel the current root operation"),
    TerminalCommand("archive", "Archive the current inactive Thread"),
    TerminalCommand("help", "Show commands and keyboard controls"),
    TerminalCommand("exit", "Exit the terminal workstation"),
)
_COMMANDS_BY_NAME = {item.name: item for item in COMMANDS}


def match_slash_command(text: str) -> TerminalCommand | None:
    value = text.strip()
    if not value.startswith("/") or any(character.isspace() for character in value):
        return None
    return _COMMANDS_BY_NAME.get(value[1:])


def command_available(
    command: TerminalCommand,
    state: TerminalState,
    *,
    context_key: str | None = None,
) -> bool:
    key = _context_key(state, context_key)
    view = _context_view(state, key)
    if command.name == "new":
        return state.launch_project_id is not None or state.draft_defaults.project_id is not None
    if command.name == "cancel":
        return view is not None and view.control_mode in {ControlMode.PREPARING, ControlMode.RUNNING}
    if command.name == "archive":
        return (
            view is not None
            and view.detail is not None
            and not view.detail.thread.archived
            and view.control_mode is ControlMode.IDLE
        )
    if command.name == "editor":
        return state.lifecycle is TerminalLifecycle.READY
    return True


def command_intent(
    name: str,
    state: TerminalState,
    *,
    context_key: str | None = None,
) -> TerminalIntent | None:
    command = _COMMANDS_BY_NAME.get(name)
    key = _context_key(state, context_key)
    if command is None or not command_available(command, state, context_key=key):
        return None
    if name == "new":
        project_id = state.launch_project_id or state.draft_defaults.project_id
        defaults = state.draft_defaults.model_copy(update={"project_id": project_id})
        return StartNewDraft(defaults)
    if name == "threads":
        return OpenOverlay("threads")
    if name == "skills":
        return OpenOverlay("skills", context_key=key)
    if name == "status":
        return OpenOverlay("status", context_key=key)
    if name == "help":
        return OpenOverlay("help")
    if name == "details":
        return ToggleToolDetails()
    if name == "thinking":
        return ToggleReasoning()
    if name in {"agent", "environment", "extensions"}:
        return OpenOverlay("configuration", key=name, context_key=key)
    if name == "editor":
        return OpenExternalEditor(key)
    if name == "cancel":
        return CancelFocusedOperation()
    if name == "archive":
        view = _context_view(state, key)
        if view is None or view.detail is None:
            return None
        return ArchiveThread(thread_id=view.thread_id, expected_version=view.detail.thread.metadata_version)
    if name == "exit":
        return ExitTerminal()
    return None


def _context_key(state: TerminalState, context_key: str | None) -> str:
    if context_key is not None:
        return context_key
    return state.focused_thread_id or "new"


def _context_view(state: TerminalState, key: str) -> ThreadViewState | None:
    return None if key == "new" else state.thread_view(key)


__all__ = [
    "COMMANDS",
    "TerminalCommand",
    "command_available",
    "command_intent",
    "match_slash_command",
]
