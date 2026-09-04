"""Textual application lifetime for the Agent UI terminal workstation."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import ClassVar

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from a13n_ui.surfaces import NewThreadDefaults
from a13n_ui.tui.controller import AppContextFactory, TerminalController
from a13n_ui.tui.editor import edit_text, resolve_editor_command
from a13n_ui.tui.intents import (
    CancelFocusedOperation,
    CloseCompletions,
    CloseOverlay,
    ExitTerminal,
    OpenOverlay,
    RetryStartup,
    StartNewDraft,
    TerminalIntent,
    ToggleTopLevelMode,
)
from a13n_ui.tui.models import (
    ControlMode,
    DraftState,
    ProjectionHints,
    TerminalLifecycle,
    TerminalMode,
    TerminalState,
)
from a13n_ui.tui.screens.focus import FocusScreen
from a13n_ui.tui.screens.overlays import OverlayPane
from a13n_ui.tui.screens.review import ReviewPane
from a13n_ui.tui.screens.workbench import WorkbenchScreen
from a13n_ui.tui.widgets.completions import CompletionPopup
from a13n_ui.tui.widgets.messages import IntentRequested

_LOGGER = logging.getLogger(__name__)


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
        Binding("ctrl+p", "command_palette", "Commands", show=True),
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
        self._modal_key: tuple[object, ...] | None = None
        self._focus_restore: Widget | None = None
        self.controller = TerminalController(
            app_factory=app_factory,
            render=self._render_state,
            launch_directory=launch_directory,
            launch_thread_id=launch_thread_id,
            launch_defaults=launch_defaults,
            open_workbench=open_workbench,
            editor_callback=self._edit_external,
            exit_callback=self._request_exit,
        )

    def compose(self) -> ComposeResult:
        yield Static("Starting Agent UI...", id="terminal-status")
        yield FocusScreen()
        yield WorkbenchScreen()
        yield ReviewPane()
        yield OverlayPane()
        yield CompletionPopup()

    def on_mount(self) -> None:
        self._apply_width_class(self.size.width)
        self.query_one(FocusScreen).display = False
        self.query_one(WorkbenchScreen).display = False
        self.query_one(ReviewPane).display = False
        self.query_one(OverlayPane).display = False
        self.query_one(CompletionPopup).display = False
        self.call_after_refresh(self._start_controller)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_width_class(event.size.width)
        focus = self.query_one(FocusScreen)
        focus.apply_width(wide=event.size.width >= 120)
        thread_id = self.terminal_state.focused_thread_id
        view = None if thread_id is None else self.terminal_state.thread_view(thread_id)
        if view is not None and view.reading_anchor is not None:
            self.call_after_refresh(focus.restore_reading_anchor, view.reading_anchor)
        review = self.terminal_state.review
        if review is not None:
            pane = self.query_one(ReviewPane)
            try:
                pane.project(review, wide=event.size.width >= 120)
            except Exception:
                _LOGGER.exception("Review surface rendering failed during resize")
                pane.show_render_failure()

    async def on_state_projected(self, message: StateProjected) -> None:
        self.terminal_state = message.state
        await self._apply_projection(message.state, message.hints)

    def on_intent_requested(self, message: IntentRequested) -> None:
        message.stop()
        self._submit_intent(message.intent)

    def action_toggle_mode(self) -> None:
        self._submit_intent(ToggleTopLevelMode())

    def action_new_draft(self) -> None:
        project_id = self.terminal_state.launch_project_id or self.terminal_state.draft_defaults.project_id
        if project_id is not None:
            defaults = NewThreadDefaults.model_validate(
                {**self.terminal_state.draft_defaults.model_dump(), "project_id": project_id}
            )
            self._submit_intent(StartNewDraft(defaults))

    def action_cancel_or_exit(self) -> None:
        if self.terminal_state.completion is not None:
            self._submit_intent(CloseCompletions())
            return
        if self.terminal_state.overlays:
            self._submit_intent(CloseOverlay())
            return
        if self.terminal_state.lifecycle is not TerminalLifecycle.READY:
            self._submit_intent(ExitTerminal())
            return
        workbench = self.query_one(WorkbenchScreen)
        if self.terminal_state.mode is TerminalMode.WORKBENCH and workbench.showing_preview:
            workbench.show_list()
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
        if self.terminal_state.completion is not None:
            self._submit_intent(CloseCompletions())
        elif self.terminal_state.overlays:
            self._submit_intent(CloseOverlay())
        elif self.terminal_state.mode is TerminalMode.WORKBENCH:
            workbench = self.query_one(WorkbenchScreen)
            if workbench.showing_preview:
                workbench.show_list()

    def action_command_palette(self) -> None:
        self._submit_intent(OpenOverlay("commands"))

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
            try:
                await self.controller.handle(intent)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOGGER.exception("Unexpected terminal controller failure")
                await self.controller.fail(exc)

    async def _edit_external(self, key: str, draft: DraftState) -> str:
        del key
        command = resolve_editor_command()
        with self.suspend():
            return await edit_text(command, draft.text)

    async def _request_exit(self) -> None:
        self.exit()

    async def _render_state(self, state: TerminalState, hints: ProjectionHints) -> None:
        self.post_message(StateProjected(state, hints))

    async def _apply_projection(self, state: TerminalState, hints: ProjectionHints) -> None:
        status = self.query_one("#terminal-status", Static)
        focus = self.query_one(FocusScreen)
        workbench = self.query_one(WorkbenchScreen)
        review = self.query_one(ReviewPane)
        overlay = self.query_one(OverlayPane)
        completions = self.query_one(CompletionPopup)
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
                try:
                    await focus.project(state, hints)
                except Exception:
                    _LOGGER.exception("Focus surface rendering failed")
                    focus.show_render_failure()
            elif workbench.display:
                try:
                    await workbench.project(state)
                except Exception:
                    _LOGGER.exception("Workbench surface rendering failed")
                    workbench.show_render_failure()

        review_open = bool(
            state.lifecycle is TerminalLifecycle.READY
            and state.overlays
            and state.overlays[-1].kind == "review"
            and state.review is not None
        )
        review.display = review_open
        if review_open and state.review is not None:
            try:
                review.project(state.review, wide=self.size.width >= 120)
            except Exception:
                _LOGGER.exception("Review surface rendering failed")
                review.show_render_failure()
        overlay_open = bool(
            state.overlays
            and state.overlays[-1].kind != "review"
            and (
                state.lifecycle is TerminalLifecycle.READY
                or (state.lifecycle is TerminalLifecycle.FAILED and state.overlays[-1].kind == "exit")
            )
        )
        overlay.display = overlay_open
        if overlay_open:
            try:
                await overlay.project(state)
            except Exception:
                _LOGGER.exception("Overlay surface rendering failed")
                overlay.show_render_failure()
        completions.display = (
            state.lifecycle is TerminalLifecycle.READY and state.completion is not None and not state.overlays
        )
        if completions.display and state.completion is not None:
            try:
                await completions.project(state.completion)
            except Exception:
                _LOGGER.exception("Completion surface rendering failed")
                completions.display = False

        if review_open and state.review is not None:
            modal_key: tuple[object, ...] | None = ("review", state.review.key)
        elif overlay_open:
            modal_key = ("overlay", state.overlays[-1])
        elif completions.display and state.completion is not None:
            modal_key = (
                "completion",
                state.completion.request_version,
                state.completion.key,
            )
        else:
            modal_key = None
        if modal_key != self._modal_key:
            if self._modal_key is None and modal_key is not None:
                self._focus_restore = self.focused
            focus.disabled = modal_key is not None
            workbench.disabled = modal_key is not None
            if review_open:
                review.focus_initial()
            elif overlay_open:
                overlay.focus_initial()
            elif completions.display:
                completions.focus_initial()
            else:
                restore = self._focus_restore
                self._focus_restore = None
                if restore is not None and restore.is_attached and not restore.disabled:
                    restore.focus()
            self._modal_key = modal_key

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
        notice = state.notices[-1] if state.notices else None
        message = notice.message if notice is not None else "Agent UI could not start."
        if notice is not None and notice.code == "terminal_controller_failed":
            return f"Terminal controller failed\n\n{message}\n\nPress Ctrl+C to exit."
        return f"Startup failed\n\n{message}\n\nPress Ctrl+R to retry or Ctrl+C to exit."
    if state.lifecycle is TerminalLifecycle.CLOSING:
        return "Closing Agent UI..."
    return "Agent UI"


__all__ = ["AgentUiTerminalApp", "StateProjected"]
