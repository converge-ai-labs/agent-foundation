"""Mode-aware multiline composer."""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.widgets import Button, Static, TextArea

from a13n_ui.tui.intents import EditDraft, SubmitComposer
from a13n_ui.tui.models import ControlMode, DraftState, TerminalLifecycle
from a13n_ui.tui.widgets.messages import IntentRequested


class PromptTextArea(TextArea):
    """Text area with explicit submit and portable newline behavior."""

    class Submit(Message):
        pass

    async def on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submit())
        elif event.key in {"alt+enter", "shift+enter"}:
            event.prevent_default()
            event.stop()
            self.insert("\n")


class Composer(Container):
    """Render a draft while emitting only typed terminal intents."""

    def __init__(self) -> None:
        super().__init__(id="composer")
        self._key = "new"
        self._draft = DraftState(key="new")
        self._projecting = False

    def compose(self) -> ComposeResult:
        yield Static("Start new Thread", id="composer-label")
        yield PromptTextArea(
            placeholder="Describe what you want the Agent to do",
            soft_wrap=True,
            show_line_numbers=False,
            id="composer-editor",
        )
        with Horizontal(id="composer-actions"):
            yield Button("Send", id="composer-submit", variant="primary")

    def project(
        self,
        *,
        key: str,
        draft: DraftState,
        mode: ControlMode,
        lifecycle: TerminalLifecycle,
    ) -> None:
        self._key = key
        self._draft = draft
        label = self.query_one("#composer-label", Static)
        editor = self.query_one("#composer-editor", PromptTextArea)
        submit = self.query_one("#composer-submit", Button)
        label.update(_composer_label(mode))
        submit.label = "Steer" if mode is ControlMode.RUNNING else "Send"
        submit.disabled = (
            lifecycle is not TerminalLifecycle.READY
            or mode not in {ControlMode.DRAFT, ControlMode.IDLE, ControlMode.RUNNING}
            or not draft.text.strip()
        )
        editor.read_only = lifecycle is TerminalLifecycle.CLOSING
        if editor.text != draft.text:
            self._projecting = True
            editor.load_text(draft.text)
            editor.cursor_location = _location_for_offset(draft.text, draft.cursor)
            self._projecting = False

    def on_prompt_text_area_submit(self, event: PromptTextArea.Submit) -> None:
        event.stop()
        self.post_message(IntentRequested(SubmitComposer(self._key)))

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if self._projecting or event.text_area.id != "composer-editor":
            return
        text = event.text_area.text
        cursor = _offset_for_location(text, event.text_area.cursor_location)
        self._draft = DraftState(
            key=self._key,
            text=text,
            cursor=cursor,
            project_paths=self._draft.project_paths,
            skill_references=self._draft.skill_references,
            editor_revision=self._draft.editor_revision,
            touched=self._draft.touched,
        )
        self.post_message(
            IntentRequested(
                EditDraft(
                    key=self._key,
                    text=text,
                    cursor=cursor,
                    project_paths=self._draft.project_paths,
                    skill_references=self._draft.skill_references,
                )
            )
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "composer-submit":
            event.stop()
            self.post_message(IntentRequested(SubmitComposer(self._key)))


def _composer_label(mode: ControlMode) -> str:
    return {
        ControlMode.DRAFT: "Start new Thread",
        ControlMode.IDLE: "Send a new message",
        ControlMode.PREPARING: "Preparing - input remains a local draft",
        ControlMode.RUNNING: "Steer current run",
        ControlMode.AWAITING_DECISION: "Answer pending decisions before sending",
        ControlMode.CANCELLING: "Cancelling - waiting for terminal settlement",
        ControlMode.UNAVAILABLE: "Controls unavailable while the Thread refreshes",
    }[mode]


def _offset_for_location(text: str, location: tuple[int, int]) -> int:
    row, column = location
    lines = text.split("\n")
    return sum(len(line) + 1 for line in lines[:row]) + min(column, len(lines[row]))


def _location_for_offset(text: str, offset: int) -> tuple[int, int]:
    remaining = max(0, min(offset, len(text)))
    lines = text.split("\n")
    for row, line in enumerate(lines):
        if remaining <= len(line):
            return row, remaining
        remaining -= len(line) + 1
    return len(lines) - 1, len(lines[-1])


__all__ = ["Composer", "PromptTextArea"]
