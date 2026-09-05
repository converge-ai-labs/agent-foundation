"""Safe bounded review overlay."""

from __future__ import annotations

import json

from rich.console import Group, RenderableType
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Input, Static

from a13n_ui.surfaces import ReviewView
from a13n_ui.tui.intents import CancelChildExecution, CloseOverlay, SteerChildExecution
from a13n_ui.tui.models import ReviewState
from a13n_ui.tui.widgets.messages import IntentRequested


class ReviewPane(Container):
    """Render only App-supplied review content and explicit availability markers."""

    def __init__(self) -> None:
        super().__init__(id="review-pane")
        self._review: ReviewState | None = None

    def compose(self) -> ComposeResult:
        yield Static("Review", id="review-title", markup=False)
        with VerticalScroll(id="review-scroll"):
            yield Static(id="review-body", markup=False)
        with Horizontal(id="review-child-controls"):
            yield Input(placeholder="Steer child execution", id="review-child-message")
            yield Button("Steer", id="review-child-steer", variant="primary")
            yield Button("Cancel child", id="review-child-cancel", variant="error")
        yield Button("Back", id="review-close", variant="primary")

    def project(self, review: ReviewState, *, wide: bool) -> None:
        self._review = review
        self.query_one("#review-title", Static).update(review.view.title)
        self.query_one("#review-body", Static).update(_render_review(review.view, wide=wide))
        controls = self.query_one("#review-child-controls", Horizontal)
        controls.display = review.execution_id is not None and bool(review.available_actions)
        self.query_one("#review-child-message", Input).display = "steer" in review.available_actions
        self.query_one("#review-child-steer", Button).display = "steer" in review.available_actions
        self.query_one("#review-child-cancel", Button).display = "cancel" in review.available_actions

    def show_render_failure(self) -> None:
        self.query_one("#review-title", Static).update("Review unavailable")
        self.query_one("#review-body", Static).update(
            Text("This review could not be rendered safely. No action was taken.", style="bold red")
        )
        self.query_one("#review-child-controls", Horizontal).display = False

    def focus_initial(self) -> None:
        message = self.query_one("#review-child-message", Input)
        if message.display:
            message.focus()
        else:
            self.query_one("#review-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "review-close":
            event.stop()
            self.post_message(IntentRequested(CloseOverlay()))
        elif event.button.id == "review-child-steer":
            event.stop()
            self._steer_child()
        elif event.button.id == "review-child-cancel":
            event.stop()
            review = self._review
            if review is not None and review.thread_id is not None and review.execution_id is not None:
                self.post_message(
                    IntentRequested(
                        CancelChildExecution(
                            parent_thread_id=review.thread_id,
                            execution_id=review.execution_id,
                        )
                    )
                )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "review-child-message":
            event.stop()
            self._steer_child()

    def _steer_child(self) -> None:
        review = self._review
        message = self.query_one("#review-child-message", Input).value
        if review is not None and review.thread_id is not None and review.execution_id is not None and message.strip():
            self.post_message(
                IntentRequested(
                    SteerChildExecution(
                        parent_thread_id=review.thread_id,
                        execution_id=review.execution_id,
                        message=message,
                    )
                )
            )


def _render_review(review: ReviewView, *, wide: bool) -> RenderableType:
    parts: list[RenderableType] = []
    if review.summary:
        parts.append(Text(review.summary, style="bold"))
    if review.lifecycle == "unavailable":
        parts.append(Text(review.unavailable_reason or "Review data is unavailable.", style="yellow"))
    elif review.kind == "diff" and review.content is not None:
        parts.append(
            _split_diff(review.content) if wide and _split_usable(review.content) else _unified_diff(review.content)
        )
    elif review.kind == "json" or review.value is not None:
        source = json.dumps(review.value, ensure_ascii=False, indent=2)
        parts.append(Syntax(source, "json", word_wrap=True))
    elif review.content is not None:
        lexer = "console" if review.kind == "shell" else "text"
        parts.append(Syntax(review.content, lexer, word_wrap=True))
    else:
        parts.append(Text("No review content was supplied.", style="dim"))
    if review.truncated:
        parts.append(Text("Content was truncated by the App surface bound.", style="yellow"))
    if review.omitted:
        parts.append(Text("Some content was omitted by the App surface bound.", style="yellow"))
    return Group(*parts)


def _unified_diff(content: str) -> Syntax:
    return Syntax(content, "diff", line_numbers=True, word_wrap=False)


def _split_usable(content: str) -> bool:
    lines = content.splitlines()
    return bool(lines) and max((len(line) for line in lines), default=0) <= 100


def _split_diff(content: str) -> Table:
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column("Before", ratio=1, overflow="fold")
    table.add_column("After", ratio=1, overflow="fold")
    before_line = 0
    after_line = 0
    for line in content.splitlines():
        if line.startswith("@@") or line.startswith("diff ") or line.startswith("index "):
            table.add_row(Text(line, style="cyan"), Text(line, style="cyan"))
        elif line.startswith("---") or line.startswith("+++"):
            table.add_row(Text(line, style="bold"), Text(line, style="bold"))
        elif line.startswith("-"):
            before_line += 1
            table.add_row(Text(f"{before_line:>4} {line[1:]}", style="red"), Text(""))
        elif line.startswith("+"):
            after_line += 1
            table.add_row(Text(""), Text(f"{after_line:>4} {line[1:]}", style="green"))
        else:
            before_line += 1
            after_line += 1
            value = line[1:] if line.startswith(" ") else line
            table.add_row(
                Text(f"{before_line:>4} {value}"),
                Text(f"{after_line:>4} {value}"),
            )
    return table


__all__ = ["ReviewPane"]
