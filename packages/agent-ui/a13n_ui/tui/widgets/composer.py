"""Mode-aware multiline composer."""

from __future__ import annotations

from dataclasses import replace

from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.widgets import Button, Static, TextArea

from a13n_ui.tui.commands import match_slash_command
from a13n_ui.tui.intents import CloseCompletions, EditDraft, ExecuteCommand, RequestCompletions, SubmitComposer
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
        yield Static("Start new Thread", id="composer-label", markup=False)
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
        changed_context = self._key != key
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
        if changed_context or editor.text != draft.text:
            with editor.prevent(TextArea.Changed, TextArea.SelectionChanged):
                editor.load_text(draft.text)
                editor.cursor_location = _location_for_offset(draft.text, draft.cursor)

    def on_prompt_text_area_submit(self, event: PromptTextArea.Submit) -> None:
        event.stop()
        self._submit()

    def _submit(self) -> None:
        command = match_slash_command(self._draft.text)
        if command is not None:
            self.post_message(IntentRequested(ExecuteCommand(command.name, draft_key=self._key)))
        else:
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
        completion = completion_request(text, cursor=cursor, key=self._key)
        self.post_message(IntentRequested(completion or CloseCompletions()))

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        editor = event.text_area
        if self._projecting or editor.id != "composer-editor" or editor.text != self._draft.text:
            return
        cursor = _offset_for_location(editor.text, editor.cursor_location)
        if cursor == self._draft.cursor:
            return
        self._draft = replace(self._draft, cursor=cursor)
        self.post_message(
            IntentRequested(
                EditDraft(
                    key=self._key,
                    text=self._draft.text,
                    cursor=cursor,
                    project_paths=self._draft.project_paths,
                    skill_references=self._draft.skill_references,
                )
            )
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "composer-submit":
            event.stop()
            self._submit()


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


def completion_request(text: str, *, cursor: int, key: str) -> RequestCompletions | None:
    cursor = max(0, min(cursor, len(text)))
    start = cursor
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = cursor
    while end < len(text) and not text[end].isspace():
        end += 1
    token = text[start:cursor]
    if not token or token[0] not in {"@", "$"} or len(token) > 513:
        return None
    return RequestCompletions(
        key=key,
        kind="path" if token[0] == "@" else "skill",
        query=token[1:],
        token_start=start,
        token_end=end,
    )


__all__ = ["Composer", "PromptTextArea", "completion_request"]
