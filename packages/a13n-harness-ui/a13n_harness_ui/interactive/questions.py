"""One presentation-only question card with isolated selection and text editing."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from prompt_toolkit.application import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea

from a13n_harness_ui.surfaces import StructuredQuestionRequestView

from .decisions import DecisionInteraction
from .rendering import terminal_text
from .selection import Selection


@dataclass(frozen=True)
class QuestionRow:
    text: str
    style: str
    option: int | None = None


def wrap_question(text: str, width: int, *, indent: str = "") -> list[str]:
    """Wrap display cells without collapsing paragraphs, whitespace, or CJK text."""
    width = max(1, width)
    indent = indent[: max(0, width - 2)]
    rows: list[str] = []
    for line in terminal_text(text).expandtabs(4).split("\n"):
        row = indent
        cells = get_cwidth(row)
        for char in line:
            size = get_cwidth(char)
            if cells + size > width and cells > get_cwidth(indent):
                rows.append(row)
                row, cells = indent, get_cwidth(indent)
            row += char
            cells += size
        rows.append(row)
    return rows


class QuestionCard:
    def __init__(
        self,
        interaction: DecisionInteraction,
        selection: Selection,
        *,
        submit: Callable[[str], None],
        cancel: Callable[[], None],
    ) -> None:
        request = interaction.request
        assert isinstance(request, StructuredQuestionRequestView)
        self.interaction = interaction
        self.review_hint = (
            f"Source preview incomplete; /review {request.request_id} reads retained details"
            if request.metadata_omitted
            else ""
        )
        self.question = request.questions[interaction.question_index]
        self.selection = selection
        self.submit = submit
        self.cancel = cancel
        self.editing = False
        self.top = 0
        self.error = ""
        self._follow_focus = False
        self._geometry = (0, 0)
        self._visible: list[QuestionRow] = []
        self.editor = TextArea(multiline=True, wrap_lines=True, height=self._editor_height, style="class:input-area")
        self.control = FormattedTextControl(self.text, focusable=True, show_cursor=False)
        self.window = Window(self.control, height=self.height, wrap_lines=False, style="class:task-pane")
        self.container = HSplit(
            [
                self.window,
                ConditionalContainer(self.editor, filter=Condition(lambda: self.editing)),
                ConditionalContainer(
                    Window(FormattedTextControl(self.hint), height=1, wrap_lines=False, style="class:selection.hint"),
                    filter=Condition(lambda: get_app().output.get_size().rows >= 4),
                ),
            ]
        )
        self.bindings = self._bindings()

    def _editor_height(self) -> Dimension:
        height = min(4, max(1, get_app().output.get_size().rows // 4))
        return Dimension.exact(height)

    def height(self) -> Dimension:
        size = get_app().output.get_size()
        budget = max(1, size.rows * 2 // 3)
        if self.editing:
            budget = max(1, budget - self._editor_height().max)
        height = min(budget, len(self.rows(max(1, size.columns))))
        return Dimension.exact(max(1, height))

    def rows(self, width: int) -> list[QuestionRow]:
        rows: list[QuestionRow] = []

        def add(text: str, style: str, option: int | None = None, indent: str = "") -> None:
            rows.extend(QuestionRow(line, style, option) for line in wrap_question(text, width, indent=indent))

        add(self.interaction.title(), "class:session-selector.title")
        add(self.question.question, "")
        for index, option in enumerate(self.question.options):
            focused = self.selection.cursor == index
            marker = (
                "[x]"
                if index in self.selection.checked
                else "[ ]"
                if self.selection.multiple
                else ">"
                if focused
                else " "
            )
            style = "class:selection.focus" if focused else "class:selection"
            add(f"{marker} {index + 1}. {option.label}", style, index)
            add(option.description, "class:selection.description", index, "   ")
        custom = len(self.selection.choices)
        add(
            ("> " if self.selection.cursor == custom else "  ") + "Write your own answer…",
            "class:selection.focus" if self.selection.cursor == custom else "class:selection",
            custom,
        )
        if self.review_hint:
            add(self.review_hint, "class:warning")
        if self.error:
            add(self.error, "class:warning")
        return rows

    def text(self) -> FormattedText:
        width = max(1, get_app().output.get_size().columns)
        rows = self.rows(width)
        height = self.height().max
        if self._follow_focus or self._geometry != (width, height):
            targets = [index for index, row in enumerate(rows) if row.option == self.selection.cursor]
            if targets:
                first, last = targets[0], targets[-1]
                if first < self.top or first >= self.top + height:
                    self.top = first
                elif last >= self.top + height and last - first < height:
                    self.top = last - height + 1
            self._follow_focus = False
        self._geometry = (width, height)
        self.top = min(max(0, self.top), max(0, len(rows) - height))
        self._visible = rows[self.top : self.top + height]
        fragments = []
        for index, row in enumerate(self._visible):
            if index:
                fragments.append(("", "\n", self.mouse))
            fragments.append((row.style, row.text, self.mouse))
        return FormattedText(fragments)

    def hint(self) -> str:
        remaining = max(
            0, math.ceil(self.interaction.timeout_seconds - (time.monotonic() - self.interaction.request_started))
        )
        total = len(self.rows(max(1, get_app().output.get_size().columns)))
        position = f"{self.top + 1}-{min(total, self.top + self.height().max)}/{total}"
        keys = (
            "Enter submit · Tab/Esc choices · Ctrl+J newline"
            if self.editing
            else "↑↓/1-4 choose · Enter confirm · Tab text · Esc cancel"
        )
        if self.selection.multiple and not self.editing:
            keys += " · Space toggle"
        full = f"{remaining}s · {position} · {keys} · PgUp/PgDn scroll"
        width = get_app().output.get_size().columns
        if get_cwidth(full) <= width:
            return full
        compact = f"{remaining}s · Enter confirm · Tab {'choices' if self.editing else 'text'} · PgUp/PgDn scroll"
        if get_cwidth(compact) <= width:
            return compact
        return f"{remaining}s ↵ Tab Esc Pg↑↓"

    def focus(self) -> None:
        get_app().layout.focus(self.editor if self.editing else self.control)

    def edit(self, editing: bool) -> None:
        self.editing = editing
        self.error = ""
        self.focus()

    def move(self, offset: int) -> None:
        count = len(self.selection.choices) + 1
        if self.selection.cursor < 0:
            self.selection.cursor = 0 if offset > 0 else count - 1
        else:
            self.selection.cursor = (self.selection.cursor + offset) % count
        self._follow_focus = True
        self.error = ""

    def scroll(self, amount: int) -> None:
        self.top += amount
        self._follow_focus = False
        get_app().invalidate()

    def mouse(self, event: MouseEvent) -> None:
        if event.event_type == MouseEventType.SCROLL_UP:
            self.scroll(-3)
        elif event.event_type == MouseEventType.SCROLL_DOWN:
            self.scroll(3)
        elif event.event_type == MouseEventType.MOUSE_UP and 0 <= event.position.y < len(self._visible):
            target = self._visible[event.position.y].option
            if target is not None:
                self.selection.cursor = target
                self.edit(False)
        get_app().invalidate()

    def confirm(self) -> None:
        if not self.editing and self.selection.cursor == len(self.selection.choices):
            self.edit(True)
            return
        try:
            answer = self.editor.text if self.editing else self.selection.answer()
            if not answer.strip():
                raise ValueError("An answer is required.")
        except ValueError as exc:
            self.error = str(exc)
            self.top = len(self.rows(max(1, get_app().output.get_size().columns)))
            return
        self.submit(answer)

    def _bindings(self) -> KeyBindings:
        keys = KeyBindings()
        selecting = Condition(lambda: not self.editing)

        @keys.add("enter")
        def confirm(event: KeyPressEvent) -> None:
            self.confirm()

        @keys.add("up")
        @keys.add("down")
        def arrows(event: KeyPressEvent) -> None:
            offset = -1 if event.key_sequence[-1].key == "up" else 1
            if not self.editing:
                self.move(offset)
            elif offset < 0:
                self.editor.buffer.cursor_up()
            else:
                self.editor.buffer.cursor_down()

        @keys.add("tab")
        @keys.add("c-space")
        def toggle_editor(event: KeyPressEvent) -> None:
            self.edit(not self.editing)

        @keys.add("escape")
        def escape(event: KeyPressEvent) -> None:
            if self.editing:
                self.edit(False)
            else:
                self.cancel()

        @keys.add("c-c")
        def cancel(event: KeyPressEvent) -> None:
            self.cancel()

        @keys.add("c-j")
        @keys.add("escape", "enter")
        def newline(event: KeyPressEvent) -> None:
            self.edit(True)
            self.editor.buffer.insert_text("\n")

        @keys.add("pageup")
        @keys.add("pagedown")
        def page(event: KeyPressEvent) -> None:
            amount = max(1, self.height().max - 1)
            self.scroll(-amount if event.key_sequence[-1].key == "pageup" else amount)

        @keys.add(" ", filter=selecting)
        def space(event: KeyPressEvent) -> None:
            if self.selection.multiple and 0 <= self.selection.cursor < len(self.selection.choices):
                self.selection.toggle()

        for number in "1234":

            @keys.add(number, filter=selecting)
            def locate(event: KeyPressEvent) -> None:
                self.selection.cursor = int(event.data) - 1
                if self.selection.cursor >= len(self.selection.choices):
                    self.selection.cursor = -1
                self._follow_focus = True

        @keys.add(Keys.Any, filter=selecting)
        def type_answer(event: KeyPressEvent) -> None:
            if event.data and event.data.isprintable():
                self.edit(True)
                self.editor.buffer.insert_text(event.data)

        @keys.add(Keys.BracketedPaste)
        def paste(event: KeyPressEvent) -> None:
            self.edit(True)
            self.editor.buffer.insert_text(event.data.replace("\r\n", "\n").replace("\r", "\n"))

        return keys
