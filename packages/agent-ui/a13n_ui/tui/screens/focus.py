"""Focused root Thread screen."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Button, Static

from a13n_ui.surfaces import RootOperationStatus
from a13n_ui.tui.intents import CancelFocusedOperation, OpenOverlay, OpenWorkbench
from a13n_ui.tui.models import (
    ControlMode,
    DraftState,
    ProjectionHints,
    ReadingAnchor,
    TerminalState,
    ThreadViewState,
)
from a13n_ui.tui.widgets.composer import Composer
from a13n_ui.tui.widgets.decisions import DecisionPane
from a13n_ui.tui.widgets.inspector import FocusInspector
from a13n_ui.tui.widgets.messages import IntentRequested
from a13n_ui.tui.widgets.timeline import TimelineView


class FocusScreen(Container):
    """Render one root Thread lineage or one unpersisted new draft."""

    def __init__(self) -> None:
        super().__init__(id="focus-screen")
        self._inspector_available = False
        self._focused_thread_id: str | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="focus-header"):
            yield Static("New Thread", id="focus-identity", markup=False)
            yield Static("Draft", id="focus-activity", markup=False)
            yield Button("Cancel", id="focus-cancel", variant="error")
            yield Button("Inspect", id="focus-inspect")
            yield Button("Workbench", id="focus-workbench")
        yield Static(id="focus-operation-summary", markup=False)
        with Horizontal(id="focus-main"):
            yield Static("Start a new Thread by sending a prompt.", id="focus-draft-intro", markup=False)
            yield TimelineView()
            yield FocusInspector()
        yield DecisionPane()
        yield Composer()
        yield Static(id="focus-notice", markup=False)
        yield Static(id="focus-actions", markup=False)

    async def project(self, state: TerminalState, hints: ProjectionHints) -> None:
        thread_id = state.focused_thread_id
        self._focused_thread_id = thread_id
        view = None if thread_id is None else state.thread_view(thread_id)
        identity = self.query_one("#focus-identity", Static)
        activity = self.query_one("#focus-activity", Static)
        operation = self.query_one("#focus-operation-summary", Static)
        intro = self.query_one("#focus-draft-intro", Static)
        timeline = self.query_one(TimelineView)
        inspector = self.query_one(FocusInspector)
        decisions = self.query_one(DecisionPane)
        composer = self.query_one(Composer)
        cancel = self.query_one("#focus-cancel", Button)

        if thread_id is None:
            self._inspector_available = False
            self.apply_width(wide=self.app.has_class("width-wide"))
            identity.update(_draft_identity(state))
            activity.update("Draft")
            operation.display = False
            intro.display = True
            intro.update("Start a new root Thread by sending a prompt. Nothing is persisted before submission.")
            timeline.display = False
            inspector.display = False
            decisions.display = False
            composer.display = True
            cancel.display = False
            draft = state.draft("new") or DraftState(key="new")
            composer.project(
                key="new",
                draft=draft,
                mode=ControlMode.DRAFT,
                lifecycle=state.lifecycle,
            )
            self._project_footer(state, None, ControlMode.DRAFT)
            return

        if view is None or view.detail is None:
            self._inspector_available = False
            self.apply_width(wide=self.app.has_class("width-wide"))
            identity.update(f"Thread {thread_id}")
            activity.update("Refreshing")
            operation.display = False
            intro.display = True
            intro.update("Loading the focused Thread projection...")
            timeline.display = False
            inspector.display = False
            decisions.display = False
            composer.display = True
            cancel.display = False
            draft = state.draft(thread_id) or DraftState(key=thread_id)
            composer.project(
                key=thread_id,
                draft=draft,
                mode=ControlMode.UNAVAILABLE,
                lifecycle=state.lifecycle,
            )
            self._project_footer(state, view, ControlMode.UNAVAILABLE)
            return

        summary = view.detail.thread
        configuration = summary.configuration
        title = summary.title or summary.thread_id
        identity.update(
            f"{configuration.project_id} / {title} - {configuration.agent_source.id} - "
            f"{configuration.environment_profile_id}"
        )
        activity.update(view.control_mode.value.replace("_", " ").title())
        operation_text = _operation_summary(view)
        operation.display = bool(operation_text)
        operation.update(operation_text)
        intro.display = False
        timeline.display = True
        self._inspector_available = True
        self.apply_width(wide=self.app.has_class("width-wide"))
        await timeline.project(
            view,
            hints,
            show_reasoning=state.show_reasoning,
            show_tool_details=state.show_tool_details,
        )
        inspector.project(view)

        awaiting = (
            view.control_mode is ControlMode.AWAITING_DECISION
            and view.decisions is not None
            and view.decision_session is not None
        )
        decisions.display = awaiting
        composer.display = not awaiting
        if awaiting:
            decision_view = view.decisions
            decision_session = view.decision_session
            assert decision_view is not None
            assert decision_session is not None
            await decisions.project(
                decision_view,
                decision_session,
                stale_session=view.stale_decision_session,
            )
        else:
            draft = state.draft(thread_id) or DraftState(key=thread_id)
            composer.project(
                key=thread_id,
                draft=draft,
                mode=view.control_mode,
                lifecycle=state.lifecycle,
            )
        cancel.display = (
            view.root_operation is not None
            and "cancel" in view.root_operation.available_actions
            and view.control_mode in {ControlMode.PREPARING, ControlMode.RUNNING}
        )
        self._project_footer(state, view, view.control_mode)

    def focus_initial(self) -> None:
        composer = self.query_one(Composer)
        if composer.display:
            composer.query_one("#composer-editor").focus()
        else:
            decisions = self.query_one(DecisionPane)
            if decisions.display:
                for button in decisions.query(Button):
                    if button.display and not button.disabled:
                        button.focus()
                        break

    def apply_width(self, *, wide: bool) -> None:
        self.query_one(FocusInspector).display = self._inspector_available and wide
        self.query_one("#focus-inspect", Button).display = self._inspector_available and not wide

    def restore_reading_anchor(self, anchor: ReadingAnchor) -> None:
        self.query_one(TimelineView).restore_reading_anchor(anchor)

    def show_render_failure(self) -> None:
        self._inspector_available = False
        self.query_one("#focus-operation-summary", Static).display = False
        intro = self.query_one("#focus-draft-intro", Static)
        intro.display = True
        intro.update("The Focus surface could not be rendered safely. App-owned work continues.")
        self.query_one(TimelineView).display = False
        self.query_one(FocusInspector).display = False
        self.query_one(DecisionPane).display = False
        self.query_one(Composer).display = False
        self.query_one("#focus-cancel", Button).display = False
        self.query_one("#focus-inspect", Button).display = False
        self.query_one("#focus-actions", Static).update("Ctrl+O workbench  Ctrl+P commands")

    def _project_footer(
        self,
        state: TerminalState,
        view: ThreadViewState | None,
        mode: ControlMode,
    ) -> None:
        actions = {
            ControlMode.DRAFT: "Enter send  Alt+Enter newline  Ctrl+N new  Ctrl+O workbench",
            ControlMode.IDLE: "Enter send  Alt+Enter newline  Ctrl+P commands  Ctrl+O workbench",
            ControlMode.PREPARING: "Ctrl+C cancel  Ctrl+O workbench  input remains a draft",
            ControlMode.RUNNING: "Enter steer  Ctrl+C cancel  Ctrl+O workbench",
            ControlMode.AWAITING_DECISION: "Tab navigate  Enter activate  Esc leave decision pending",
            ControlMode.CANCELLING: "Waiting for terminal settlement  Ctrl+O workbench",
            ControlMode.UNAVAILABLE: "Refreshing App truth  Ctrl+O workbench",
        }[mode]
        self.query_one("#focus-actions", Static).update(actions)
        notice = next(
            (item for item in reversed(state.notices) if item.thread_id in {None, state.focused_thread_id}),
            None,
        )
        notice_widget = self.query_one("#focus-notice", Static)
        notice_widget.display = notice is not None
        if notice is not None:
            style = "red" if notice.severity == "error" else "yellow"
            notice_widget.update(Text(notice.message, style=style))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "focus-cancel":
            event.stop()
            self.post_message(IntentRequested(CancelFocusedOperation()))
        elif event.button.id == "focus-inspect":
            event.stop()
            self.post_message(IntentRequested(OpenOverlay("inspector", context_key=self._focused_thread_id)))
        elif event.button.id == "focus-workbench":
            event.stop()
            self.post_message(IntentRequested(OpenWorkbench()))


def _draft_identity(state: TerminalState) -> str:
    project = state.draft_defaults.project_id or state.launch_project_id
    return f"New Thread - {project}" if project is not None else "New Thread - no launch Project"


def _operation_summary(view: ThreadViewState) -> str:
    operation = view.root_operation
    if operation is None or operation.status in {RootOperationStatus.preparing, RootOperationStatus.running}:
        return ""
    parts = [f"Execution: {operation.status.value}"]
    if operation.outcome is not None:
        outcome = operation.outcome
        parts.append(f"Continuation: {outcome.continuation.status}")
        parts.append(
            "Environment: "
            f"{outcome.environment.published} published, "
            f"{outcome.environment.unchanged} unchanged, "
            f"{outcome.environment.failed} failed"
        )
        if outcome.environment.cleanup_failures:
            parts.append(f"Cleanup failures: {len(outcome.environment.cleanup_failures)}")
        if outcome.execution.output_omitted:
            parts.append("Execution output omitted")
    if operation.failure is not None:
        parts.append(operation.failure.message)
    return " | ".join(parts)


__all__ = ["FocusScreen"]
