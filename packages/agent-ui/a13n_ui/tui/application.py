"""Textual application lifetime for the Agent UI terminal workstation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.widgets import Static

from a13n_ui.surfaces import NewThreadDefaults
from a13n_ui.tui.controller import AppContextFactory, TerminalController
from a13n_ui.tui.intents import (
    CancelFocusedOperation,
    CloseOverlay,
    ExitTerminal,
    RetryStartup,
    StartNewDraft,
    TerminalIntent,
    ToggleTopLevelMode,
)
from a13n_ui.tui.models import (
    ControlMode,
    ProjectionHints,
    TerminalLifecycle,
    TerminalMode,
    TerminalState,
)
from a13n_ui.tui.screens.focus import FocusScreen
from a13n_ui.tui.screens.review import ReviewPane
from a13n_ui.tui.widgets.messages import IntentRequested


class StateProjected(Message):
    """One coalesced immutable state projection for the Textual message loop."""

    def __init__(self, state: TerminalState, hints: ProjectionHints) -> None:
        super().__init__()
        self.state = state
        self.hints = hints


class AgentUiTerminalApp(App[None]):
    """Mount first, then let one controller open and own the process-local App."""

    CSS_PATH = "styles/terminal.tcss"
    TITLE = "Agent UI"
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+p", "noop", "Commands", show=True),
        Binding("ctrl+o", "toggle_mode", "Workbench", show=True),
        Binding("ctrl+n", "new_draft", "New", show=True),
        Binding("ctrl+r", "retry", "Retry", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("ctrl+c", "cancel_or_exit", "Cancel / Exit", show=True, priority=True),
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
        self._intent_lock = asyncio.Lock()
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
        yield Static("Starting Agent UI...", id="terminal-status")
        yield FocusScreen()
        yield Static("Workbench", id="workbench-screen")
        yield ReviewPane()

    def on_mount(self) -> None:
        self._apply_width_class(self.size.width)
        self.query_one(FocusScreen).display = False
        self.query_one("#workbench-screen", Static).display = False
        self.query_one(ReviewPane).display = False
        self.call_after_refresh(self._start_controller)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_width_class(event.size.width)
        review = self.terminal_state.review
        if review is not None:
            self.query_one(ReviewPane).project(review, wide=event.size.width >= 120)

    async def on_state_projected(self, message: StateProjected) -> None:
        self.terminal_state = message.state
        await self._apply_projection(message.state, message.hints)

    def on_intent_requested(self, message: IntentRequested) -> None:
        message.stop()
        self._submit_intent(message.intent)

    def action_toggle_mode(self) -> None:
        self._submit_intent(ToggleTopLevelMode())

    def action_new_draft(self) -> None:
        if self.terminal_state.launch_project_id is not None:
            self._submit_intent(StartNewDraft())

    def action_cancel_or_exit(self) -> None:
        if self.terminal_state.overlays:
            self._submit_intent(CloseOverlay())
            return
        view = (
            None
            if self.terminal_state.focused_thread_id is None
            else self.terminal_state.thread_view(self.terminal_state.focused_thread_id)
        )
        if view is not None and view.control_mode in {ControlMode.PREPARING, ControlMode.RUNNING}:
            self._submit_intent(CancelFocusedOperation())
        else:
            self._submit_intent(ExitTerminal())

    async def action_back(self) -> None:
        if self.terminal_state.overlays:
            self._submit_intent(CloseOverlay())

    def action_noop(self) -> None:
        pass

    def action_retry(self) -> None:
        self._submit_intent(RetryStartup(), group="terminal-startup")

    def _start_controller(self) -> None:
        self.run_worker(
            self.controller.start(),
            name="terminal-startup",
            group="terminal-startup",
            exclusive=True,
        )

    def _submit_intent(self, intent: TerminalIntent, *, group: str = "terminal-intents") -> None:
        self.run_worker(
            self._handle_intent(intent),
            name=f"terminal-intent-{type(intent).__name__}",
            group=group,
        )

    async def _handle_intent(self, intent: TerminalIntent) -> None:
        async with self._intent_lock:
            await self.controller.handle(intent)

    async def _request_exit(self) -> None:
        self.exit()

    async def _render_state(self, state: TerminalState, hints: ProjectionHints) -> None:
        self.post_message(StateProjected(state, hints))

    async def _apply_projection(self, state: TerminalState, hints: ProjectionHints) -> None:
        status = self.query_one("#terminal-status", Static)
        focus = self.query_one(FocusScreen)
        workbench = self.query_one("#workbench-screen", Static)
        review = self.query_one(ReviewPane)
        if state.lifecycle is not TerminalLifecycle.READY:
            status.display = True
            status.update(_status_text(state))
            focus.display = False
            workbench.display = False
        else:
            status.display = False
            focus.display = state.mode is TerminalMode.FOCUS
            workbench.display = state.mode is TerminalMode.WORKBENCH
            if focus.display:
                await focus.project(state, hints)
            elif workbench.display:
                workbench.update(_workbench_placeholder(state))

        review_open = bool(state.overlays and state.overlays[-1].kind == "review" and state.review is not None)
        review.display = review_open
        if review_open and state.review is not None:
            review.project(state.review, wide=self.size.width >= 120)

    def _apply_width_class(self, width: int) -> None:
        self.remove_class("width-wide", "width-medium", "width-narrow")
        if width >= 120:
            self.add_class("width-wide")
        elif width >= 80:
            self.add_class("width-medium")
        else:
            self.add_class("width-narrow")


def _status_text(state: TerminalState) -> str:
    if state.lifecycle is TerminalLifecycle.STARTING:
        return "Starting Agent UI..."
    if state.lifecycle is TerminalLifecycle.FAILED:
        message = state.notices[-1].message if state.notices else "Agent UI could not start."
        return f"Startup failed\n\n{message}\n\nPress Ctrl+R to retry or Ctrl+C to exit."
    if state.lifecycle is TerminalLifecycle.CLOSING:
        return "Closing Agent UI..."
    return "Agent UI"


def _workbench_placeholder(state: TerminalState) -> str:
    count = len(state.workbench.rows)
    project = state.project_filter_id or "All Projects"
    return f"Workbench - {project}\n\n{count} Thread(s)"


__all__ = ["AgentUiTerminalApp", "StateProjected"]
