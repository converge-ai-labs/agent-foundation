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

from a13n_ui.app import AgentUiApp
from a13n_ui.errors import AppStateError
from a13n_ui.surfaces import NewThreadDefaults
from a13n_ui.tui.controller import AppContextFactory, TerminalAppProtocol, TerminalController
from a13n_ui.tui.editor import edit_text, resolve_editor_command
from a13n_ui.tui.intents import (
    CancelFocusedOperation,
    CloseCompletions,
    CloseOverlay,
    ExitTerminal,
    OpenOverlay,
    RetryStartup,
    SelectConfigurationResource,
    StartNewDraft,
    SubmitComposer,
    TerminalIntent,
)
from a13n_ui.tui.models import (
    ControlMode,
    DraftState,
    ProjectionHints,
    TerminalLifecycle,
    TerminalState,
)
from a13n_ui.tui.screens.focus import FocusScreen
from a13n_ui.tui.screens.overlays import OverlayPane
from a13n_ui.tui.screens.readiness import ReadinessScreen
from a13n_ui.tui.screens.review import ReviewPane
from a13n_ui.tui.screens.setup import SetupScreen
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
        Binding("ctrl+o", "thread_picker", "Threads", show=True),
        Binding("ctrl+n", "new_draft", "New", show=True),
        Binding("ctrl+r", "retry", "Retry", show=False),
        Binding("escape", "back", "Back", show=False),
        Binding("ctrl+c", "cancel_or_exit", "Cancel / Exit", show=True, priority=True),
        Binding("down", "completion_next", show=False, priority=True),
        Binding("up", "completion_previous", show=False, priority=True),
        Binding("enter", "completion_accept", show=False, priority=True),
    ]

    def __init__(
        self,
        *,
        app_factory: AppContextFactory,
        launch_directory: Path,
        launch_thread_id: str | None = None,
        launch_defaults: NewThreadDefaults | None = None,
        show_setup: bool = False,
    ) -> None:
        super().__init__()
        self.terminal_state = TerminalState(draft_defaults=launch_defaults or NewThreadDefaults())
        self._intent_lock = asyncio.Lock()
        self._modal_key: tuple[object, ...] | None = None
        self._route_key: tuple[object, ...] | None = None
        self._focus_restore: Widget | None = None
        self._application: AgentUiApp | None = None
        self._preflight_open = False
        self._checked_roots: set[str] = set()
        self._setup_open = False
        self._show_setup = show_setup
        self._launch_directory = launch_directory
        self.controller = TerminalController(
            app_factory=app_factory,
            render=self._render_state,
            launch_directory=launch_directory,
            launch_thread_id=launch_thread_id,
            launch_defaults=launch_defaults,
            editor_callback=self._edit_external,
            exit_callback=self._request_exit,
            setup_callback=self._setup,
        )

    async def _setup(self, application: TerminalAppProtocol, force: bool) -> None:
        if self._setup_open or not isinstance(application, AgentUiApp):
            return
        self._application = application
        self._setup_open = True
        try:
            try:
                status = await application.setup_status()
            except AppStateError as exc:
                if exc.code == "configuration_path_unavailable" and not force:
                    return
                raise
            if not force and not self._show_setup and not status.needed:
                return
            completed: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

            def dismissed(result: bool | None) -> None:
                if not completed.done():
                    completed.set_result(bool(result))

            await self.push_screen(SetupScreen(application, directory=self._launch_directory, status=status), dismissed)
            await completed
        finally:
            self._setup_open = False

    def compose(self) -> ComposeResult:
        yield Static("Starting Agent UI...", id="terminal-status", markup=False)
        yield FocusScreen()
        yield ReviewPane()
        yield OverlayPane()
        yield CompletionPopup()

    def on_mount(self) -> None:
        self._apply_width_class(self.size.width)
        self.query_one(FocusScreen).display = False
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

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action.startswith("completion_"):
            return self.terminal_state.completion is not None and not self.terminal_state.overlays
        return super().check_action(action, parameters)

    def action_completion_next(self) -> None:
        self.query_one(CompletionPopup).move_selection(1)

    def action_completion_previous(self) -> None:
        self.query_one(CompletionPopup).move_selection(-1)

    def action_completion_accept(self) -> None:
        self.query_one(CompletionPopup).accept_selection()

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

    def action_thread_picker(self) -> None:
        self._submit_intent(OpenOverlay("threads"))

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
        if self._preflight_open:
            return
        self.run_worker(
            self._handle_intent(intent),
            name=f"terminal-intent-{type(intent).__name__}",
            group=group,
        )

    async def _handle_intent(self, intent: TerminalIntent) -> None:
        try:
            if isinstance(intent, (SelectConfigurationResource, SubmitComposer)):
                prepared = await self._prepare_environment_intent(intent)
                if prepared is None:
                    return
                intent = prepared
            async with self._intent_lock:
                await self.controller.handle(intent)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOGGER.exception("Unexpected terminal controller failure")
            await self.controller.fail(exc)

    async def _prepare_environment_intent(
        self,
        intent: SelectConfigurationResource | SubmitComposer,
    ) -> SelectConfigurationResource | SubmitComposer | None:
        application = self._application
        if application is None or self._preflight_open:
            return intent if application is None else None
        selecting = isinstance(intent, SelectConfigurationResource)
        if selecting and (intent.kind != "environment" or intent.resource_id != "environment-sandbox"):
            return intent
        state = self.controller.state
        thread_id = state.focused_thread_id
        view = state.thread_view(thread_id) if thread_id else None
        if not selecting and view is not None and view.control_mode is not ControlMode.IDLE:
            return intent  # Steering never prepares a replacement Environment.
        self._preflight_open = True
        try:
            if view is not None and view.detail is not None:
                configuration = view.detail.thread.configuration
                project_id = configuration.project_id
                profile_id = configuration.environment_profile_id
            else:
                status = await application.setup_status()
                project_id = state.draft_defaults.project_id or state.launch_project_id or status.default_project
                profile_id = state.draft_defaults.environment_profile_id or status.environment_profile
            if not selecting and profile_id != "environment-sandbox":
                return intent
            projects = await application.projects()
            roots = next((project.roots for project in projects if project.project_id == project_id), ())
            if not roots or (not selecting and set(roots) <= self._checked_roots):
                return intent
            completed: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()

            def dismissed(result: str | None) -> None:
                if not completed.done():
                    completed.set_result(result)

            await self.push_screen(ReadinessScreen(application, roots), dismissed)
            selected = await completed
            if (
                selected is None
                or self.controller.state.focused_thread_id != thread_id
                or self.controller.state.draft_defaults != state.draft_defaults
            ):
                return None
            if selected == "environment-sandbox":
                self._checked_roots.update(roots)
                return intent
            # Full Control is an explicit normal selection; keep the prompt and
            # require a separate Send rather than admitting during recovery.
            return SelectConfigurationResource(kind="environment", resource_id=selected)
        finally:
            self._preflight_open = False

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
        review = self.query_one(ReviewPane)
        overlay = self.query_one(OverlayPane)
        completions = self.query_one(CompletionPopup)
        if state.lifecycle is not TerminalLifecycle.READY:
            status.display = True
            status.update(_status_text(state))
            focus.display = False
        else:
            status.display = False
            focus.display = True
            if focus.display:
                try:
                    await focus.project(state, hints)
                except Exception:
                    _LOGGER.exception("Focus surface rendering failed")
                    focus.show_render_failure()

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
                state.completion.key,
            )
        else:
            modal_key = None
        if modal_key != self._modal_key:
            if self._modal_key is None and modal_key is not None:
                self._focus_restore = self.focused
            focus.disabled = review_open or overlay_open
            if review_open:
                review.focus_initial()
            elif overlay_open:
                overlay.focus_initial()
            elif not completions.display:
                restore = self._focus_restore
                self._focus_restore = None
                if restore is not None and restore.is_attached and not restore.disabled:
                    restore.focus()
            self._modal_key = modal_key

        if state.lifecycle is TerminalLifecycle.READY and modal_key is None:
            thread = None if state.focused_thread_id is None else state.thread_view(state.focused_thread_id)
            route_key = (
                state.focused_thread_id,
                thread is not None and thread.control_mode is ControlMode.AWAITING_DECISION,
            )
            if self._route_key != route_key:
                self.call_after_refresh(focus.focus_initial)
                self._route_key = route_key

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
