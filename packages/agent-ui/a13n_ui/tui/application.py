"""Textual application lifetime for the Agent UI terminal workstation."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import Static

from a13n_ui.surfaces import NewThreadDefaults
from a13n_ui.tui.controller import AppContextFactory, TerminalController
from a13n_ui.tui.intents import ExitTerminal, RetryStartup, ToggleTopLevelMode
from a13n_ui.tui.models import ProjectionHints, TerminalLifecycle, TerminalMode, TerminalState


class AgentUiTerminalApp(App[None]):
    """Mount first, then let one controller open and own the process-local App."""

    CSS_PATH = "styles/terminal.tcss"
    TITLE = "Agent UI"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+p", "noop", "Commands", show=True),
        Binding("ctrl+o", "toggle_mode", "Workbench", show=True),
        Binding("ctrl+r", "retry", "Retry", show=False),
        Binding("ctrl+c", "quit_terminal", "Exit", show=True, priority=True),
    ]

    def __init__(
        self,
        *,
        app_factory: AppContextFactory,
        launch_directory: Path,
        launch_thread_id: str | None = None,
        launch_defaults: NewThreadDefaults | None = None,
        open_workbench: bool = False,
    ) -> None:
        super().__init__()
        self.terminal_state = TerminalState(draft_defaults=launch_defaults or NewThreadDefaults())
        self.controller = TerminalController(
            app_factory=app_factory,
            render=self._render_state,
            launch_directory=launch_directory,
            launch_thread_id=launch_thread_id,
            launch_defaults=launch_defaults,
            open_workbench=open_workbench,
            exit_callback=self._request_exit,
        )

    def compose(self) -> ComposeResult:
        yield Static("Starting Agent UI...", id="terminal-root")

    def on_mount(self) -> None:
        self.call_after_refresh(self._start_controller)

    async def action_toggle_mode(self) -> None:
        await self.controller.handle(ToggleTopLevelMode())

    async def action_quit_terminal(self) -> None:
        await self.controller.handle(ExitTerminal())

    def action_noop(self) -> None:
        pass

    def action_retry(self) -> None:
        self.run_worker(
            self.controller.handle(RetryStartup()),
            name="terminal-startup-retry",
            group="terminal-startup",
            exclusive=True,
        )

    def _start_controller(self) -> None:
        self.run_worker(
            self.controller.start(),
            name="terminal-startup",
            group="terminal-startup",
            exclusive=True,
        )

    async def _request_exit(self) -> None:
        self.exit()

    async def _render_state(self, state: TerminalState, hints: ProjectionHints) -> None:
        del hints
        self.terminal_state = state
        root = self.query_one("#terminal-root", Static)
        root.update(_status_text(state))


def _status_text(state: TerminalState) -> str:
    if state.lifecycle is TerminalLifecycle.STARTING:
        return "Starting Agent UI..."
    if state.lifecycle is TerminalLifecycle.FAILED:
        message = state.notices[-1].message if state.notices else "Agent UI could not start."
        return f"Startup failed\n\n{message}\n\nPress Ctrl+R to retry or Ctrl+C to exit."
    if state.lifecycle is TerminalLifecycle.CLOSING:
        return "Closing Agent UI..."
    if state.mode is TerminalMode.WORKBENCH:
        count = len(state.workbench.rows)
        project = state.project_filter_id or "All Projects"
        return f"Workbench - {project}\n\n{count} Thread(s)"
    if state.focused_thread_id is None:
        project = state.launch_project_id or "No launch Project"
        return f"New Thread - {project}"
    return f"Focus - {state.focused_thread_id}"


__all__ = ["AgentUiTerminalApp"]
