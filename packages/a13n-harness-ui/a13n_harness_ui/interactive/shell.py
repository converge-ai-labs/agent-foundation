"""One native full-terminal Application; HarnessUiApp retains all authority."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine, Generator
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from anyio import CancelScope
from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import ConditionalKeyBindings, KeyBindings, KeyPressEvent, merge_key_bindings
from prompt_toolkit.key_binding.key_bindings import DynamicKeyBindings, KeyBindingsBase
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    DynamicContainer,
    Float,
    FloatContainer,
    HSplit,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import Processor
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea

from a13n_harness_ui.environment_profiles import WINDOWS_EXECUTION_NOTICE, local_sandbox_supported
from a13n_harness_ui.thread_files import AttachmentUpload

from .attachments import add_images, clipboard_images, read_attachment
from .commands import CommandRegistry, Invocation
from .composer import ComposerWindow, wrapped_height
from .diagnostics import exception_report, pending_task_warning
from .history import HistoryBrowser
from .inline_attachments import AttachmentBuffer, AttachmentClipboard, AttachmentProcessor, InlineAttachments
from .local_shell import run_local_shell, validate_local_shell_support
from .pastes import PendingPastes
from .questions import QuestionCard
from .rendering import Status, StreamRenderer, terminal_text
from .resume import ResumeBrowser
from .selection import Choice, Selection, resolve_choice
from .theme import prompt_toolkit_style_rules, resolve_theme
from .transcript import TranscriptControl

if TYPE_CHECKING:
    from a13n_harness_ui.cli import CliRequest
    from a13n_harness_ui.model_accounts.usage import ResetRequest
    from a13n_harness_ui.surfaces import ThreadDeferredResponse

    from .backend import SessionBackend
    from .decisions import DecisionInteraction


class SlashCompleter(Completer):
    def __init__(self, registry: CommandRegistry) -> None:
        self.registry = registry

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Generator[Completion]:
        text = document.text_before_cursor
        prefix = text.split()[-1] if text and not text[-1].isspace() else ""
        for value, help_text in self.registry.completions(text):
            yield Completion(value, start_position=-len(prefix), display_meta=help_text)


class CliShell:
    def __init__(
        self,
        request: CliRequest,
        *,
        directory: Path | None = None,
        status: Status | None = None,
    ) -> None:
        self.request = request
        self.directory = directory or Path.cwd()
        self.registry = CommandRegistry()
        self.status = status or Status(mode=request.display or "concise", mode_explicit=request.display is not None)
        self.status.directory = self.directory.absolute()
        self.renderer = StreamRenderer(self.status)
        self.backend: SessionBackend | None = None
        self._activity_thread: str | None = None
        self._notes_thread: str | None = None
        self._subagent_total: int | None = 0
        self.pending_codex_reset: ResetRequest | None = None
        self.history_browser: HistoryBrowser | None = None
        self.resume_browser: ResumeBrowser | None = None
        self.ready = False
        self.closing = False
        self.job: asyncio.Task[None] | None = None
        self.job_kind: str | None = None
        self._input_task: asyncio.Task[None] | None = None
        self.interaction: DecisionInteraction | None = None
        self.selection: Selection | None = None
        self.question_card: QuestionCard | None = None
        self.selector_focused = True
        self.menu_title = ""
        self.menu_handler: Callable[[str | tuple[str, ...]], Awaitable[None]] | None = None
        self._saved_draft: Document | None = None
        self.pastes = PendingPastes()
        self.mouse = True
        self.inline = InlineAttachments()
        self.clipboard = AttachmentClipboard()
        self._draft_generation = 0
        self._recoverable: Document | None = None
        self._submitted_draft: Document | None = None
        self._sending_draft: Document | None = None
        self._clipboard_task: asyncio.Task[None] | None = None
        self._last_interrupt = float("-inf")
        self.view = TranscriptControl(self.renderer.transcript)
        self.composer = TextArea(
            multiline=True,
            completer=SlashCompleter(self.registry),
            complete_while_typing=True,
        )
        self.composer.buffer = AttachmentBuffer(self.inline, SlashCompleter(self.registry))
        self.composer.control.buffer = self.composer.buffer
        processors: list[Processor] = [AttachmentProcessor(self.inline)]
        self.composer.control.input_processors = processors
        self.composer.window = ComposerWindow(
            self.composer.control,
            style="class:text-area class:input-area",
            get_line_prefix=lambda line, wrap: FormattedText(
                [("class:input-area.prompt", " > ")]
                if line == 0 and wrap == 0
                else [("class:input-area.continuation", "   " if wrap else " · ")]
            ),
            wrap_lines=True,
            height=self._composer_height,
        )
        self.composer.buffer.on_text_changed += self._draft_changed
        self.composer.buffer.on_cursor_position_changed += self._paste_cursor_changed
        original_mouse_handler = self.composer.control.mouse_handler

        def composer_mouse(mouse_event: MouseEvent):
            if mouse_event.event_type in {MouseEventType.MOUSE_DOWN, MouseEventType.MOUSE_UP}:
                self.selector_focused = False
            return original_mouse_handler(mouse_event)

        self.composer.control.mouse_handler = composer_mouse
        self.output_window = Window(
            self.view, wrap_lines=False, always_hide_cursor=True, get_vertical_scroll=lambda window: self.view.top
        )
        panel = ConditionalContainer(
            Window(
                FormattedTextControl(self._panel), height=self._panel_height, wrap_lines=True, style="class:task-pane"
            ),
            filter=Condition(
                lambda: (
                    self.app.output.get_size().rows >= 3
                    and self.question_card is None
                    and (self.interaction is not None or self.selection is not None)
                )
            ),
        )
        layout = FloatContainer(
            HSplit(
                [
                    self.output_window,
                    ConditionalContainer(
                        HSplit(
                            [
                                Window(height=1, char="─", style="class:input-area.border"),
                                Window(
                                    FormattedTextControl(self._task_text),
                                    height=lambda: min(7, len(self.renderer.tasks.lines())),
                                    wrap_lines=False,
                                    style="class:task-pane",
                                ),
                            ]
                        ),
                        filter=Condition(
                            lambda: (
                                self.question_card is None
                                and bool(self.renderer.tasks.lines())
                                and self.app.output.get_size().rows >= 16
                            )
                        ),
                    ),
                    ConditionalContainer(
                        HSplit(
                            [
                                Window(height=1, char="─", style="class:frame.border"),
                                Window(FormattedTextControl(self._activity_text), height=1, style="class:status-bar"),
                            ]
                        ),
                        filter=Condition(
                            lambda: (
                                self.question_card is None
                                and bool(self._activity_hint())
                                and self.app.output.get_size().rows >= 12
                            )
                        ),
                    ),
                    panel,
                    ConditionalContainer(
                        DynamicContainer(lambda: self.question_card.container if self.question_card else Window()),
                        filter=Condition(lambda: self.question_card is not None),
                    ),
                    ConditionalContainer(
                        Window(FormattedTextControl(self._toolbar), height=1, style="class:status-bar"),
                        filter=Condition(
                            lambda: (
                                self.question_card is None
                                and self.status.show_status
                                and self.app.output.get_size().rows >= 8
                            )
                        ),
                    ),
                    ConditionalContainer(
                        Window(FormattedTextControl(self._composer_header), height=1, style="class:input-area.border"),
                        filter=Condition(lambda: self.question_card is None and self.app.output.get_size().rows >= 8),
                    ),
                    ConditionalContainer(self.composer, filter=Condition(lambda: self.question_card is None)),
                    ConditionalContainer(
                        Window(
                            FormattedTextControl(self._hints),
                            height=lambda: len(self._hints().splitlines()),
                            style="class:session-selector.hint",
                        ),
                        filter=Condition(lambda: self.question_card is None and self.app.output.get_size().rows >= 12),
                    ),
                ]
            ),
            floats=[Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=8, scroll_offset=1))],
        )
        self.history_window = Window(
            FormattedTextControl(""),
            always_hide_cursor=True,
            get_vertical_scroll=lambda window: self.history_browser.view.top if self.history_browser else 0,
        )
        layout = HSplit(
            [
                ConditionalContainer(
                    layout, filter=Condition(lambda: self.history_browser is None and self.resume_browser is None)
                ),
                ConditionalContainer(
                    HSplit(
                        [
                            DynamicContainer(
                                lambda: self.resume_browser.container if self.resume_browser else Window()
                            ),
                            Window(
                                FormattedTextControl(self._hints),
                                height=lambda: len(self._hints().splitlines()),
                                style="class:session-selector.hint",
                            ),
                        ]
                    ),
                    filter=Condition(lambda: self.resume_browser is not None and self.history_browser is None),
                ),
                ConditionalContainer(
                    HSplit(
                        [
                            Window(
                                FormattedTextControl(
                                    lambda: self.history_browser.title() if self.history_browser else ""
                                ),
                                height=1,
                                style="class:session-selector.title",
                            ),
                            self.history_window,
                            Window(
                                FormattedTextControl(self._hints),
                                height=lambda: len(self._hints().splitlines()),
                                style="class:session-selector.hint",
                            ),
                        ]
                    ),
                    filter=Condition(lambda: self.history_browser is not None),
                ),
            ]
        )
        self.app: Application[None] = Application(
            clipboard=self.clipboard,
            layout=Layout(layout, focused_element=self.composer),
            key_bindings=self._bindings(),
            full_screen=True,
            mouse_support=Condition(lambda: self.mouse),
            style=self._style(),
            min_redraw_interval=1 / 15,
        )

    def _draft_changed(self, buffer: object) -> None:
        self._last_interrupt = float("-inf")

    def _paste_cursor_changed(self, buffer: Buffer) -> None:
        expanded = self.pastes.edit(buffer.text, buffer.cursor_position)
        if expanded is not None:
            text, position = expanded
            buffer.document = Document(text, position)

    def _style(self) -> Style:
        rules = prompt_toolkit_style_rules(self.renderer.transcript.theme)
        rules.update(
            {
                "selection.focus": rules["session-selector.selection"],
                "selection.description": rules["session-selector.hint"],
                "selection.hint": rules["session-selector.key"],
                "warning": rules["status-bar.warning"],
                "input-area.attachment": rules["session-selector.key"],
            }
        )
        return Style.from_dict(rules)

    def _composer_height(self) -> Dimension:
        size = self.app.output.get_size()
        # Match BufferControl's trailing cursor cell and the window's wrapping.
        rows = sum(
            wrapped_height(line + " ", size.columns) for line in self.inline.display(self.composer.text).split("\n")
        )
        minimum = 3 if size.rows >= 16 else 1
        height = min(max(1, size.rows // 3), max(minimum, min(7, rows)))
        return Dimension(min=1, preferred=height, max=height)

    def _panel_height(self) -> Dimension:
        available = max(1, self.app.output.get_size().rows // 2)
        choices = min(8, len(self.selection.choices)) + 1 if self.selection else 0
        height = min(available, choices + 1 if self.selection else 4)
        return Dimension(min=1, preferred=height, max=height)

    def _panel(self) -> FormattedText:
        prompt = self.interaction.prompt() if self.interaction else self.menu_title or "Choose an option"
        # Full context remains scrollable above. Keep the decision and composer
        # usable on short terminals rather than allowing a panel to own the screen.
        title = self.interaction.title() if self.interaction else prompt.split("\n")[0]
        fragments = [("class:session-selector.title", terminal_text(title) + "\n")]
        if self.selection is not None:
            available = max(1, self._panel_height().max - 2)
            fragments.extend(
                self.selection.lines(
                    max_choices=min(8, available), descriptions=self.app.output.get_size().columns >= 80
                )
            )
        else:
            fragments.append(("", terminal_text(prompt) + "\n"))
        # One physical row per choice: long descriptions must not hide the
        # focused approval or push the composer out of a short terminal.
        width = max(1, self.app.output.get_size().columns - 1)
        clipped = []
        used = 0
        for style, text in fragments:
            for char in terminal_text(text):
                if char == "\n":
                    clipped.append((style, char))
                    used = 0
                elif used + get_cwidth(char) <= width:
                    clipped.append((style, char))
                    used += get_cwidth(char)
        return FormattedText([(style, text, self._panel_mouse) for style, text in clipped])

    def _panel_mouse(self, event: MouseEvent) -> None:
        if self.selection is None:
            return
        count = min(8, max(1, self._panel_height().max - 2))
        if event.event_type == MouseEventType.SCROLL_UP:
            self.selection.scroll(-3, count)
        elif event.event_type == MouseEventType.SCROLL_DOWN:
            self.selection.scroll(3, count)
        elif event.event_type == MouseEventType.MOUSE_UP:
            row = event.position.y - 1
            index = self.selection.start(count) + row
            if 0 <= row < count and 0 <= index < len(self.selection.choices):
                self.selection.cursor = index
                self.selector_focused = True
        self.app.invalidate()

    def _task_text(self) -> FormattedText:
        width = max(1, self.app.output.get_size().columns)
        fragments = []
        states = {"active": "running", "blocked": "waiting", "pending": "muted", "done": "completed"}
        for index, line in enumerate(self.renderer.tasks.lines(width)):
            line = terminal_text(line).expandtabs(4)
            row = []
            if index == 0:
                heading, separator, hint = line.partition(" · F2")
                row.append(("class:task-pane.heading", heading))
                if separator:
                    row.append(("class:activity.muted", " · F2" + hint))
            elif line.startswith("["):
                state, _, body = line[1:].partition("] ")
                row.append((f"class:activity.{states.get(state, 'muted')} bold", f"[{state}] "))
                identifier, separator, subject = body.partition(" · ")
                row.append(("class:activity.muted", identifier + separator))
                subject, separator, waits = subject.partition(" · waits ")
                style = "bold" if state == "active" else "class:activity.muted" if state == "done" else ""
                row.append((style, subject))
                if separator:
                    row.append(("class:activity.waiting", separator + waits))
            else:
                row.append(("class:activity.muted", line))
            # One physical row per task; signal truncation without splitting wide
            # characters or letting a long subject hide the next task's state.
            clipped = get_cwidth(line) > width
            remaining = width - int(clipped)
            for style, text in row:
                for char in text:
                    cells = get_cwidth(char)
                    if cells > remaining:
                        remaining = 0
                        break
                    fragments.append((style, char))
                    remaining -= cells
                if remaining <= 0:
                    break
            if clipped:
                fragments.append(("class:activity.muted", "…"))
            fragments.append(("", "\n"))
        return FormattedText(fragments)

    def _activity_hint(self, *, compact: bool = False) -> str:
        hints = []
        thread_id = self.backend.thread_id if self.backend is not None else None
        count = self.renderer.note_count
        if thread_id is not None and self._notes_thread == thread_id and count is not None:
            hints.append(f"Notes {count} · /notes")
        if thread_id is not None and self._activity_thread == thread_id:
            if self._subagent_total is None:
                hints.append("Subagents unavailable · /subagents")
            elif self._subagent_total:
                hints.append(f"Subagents {self._subagent_total} total · /subagents")
        if self.renderer.background_hint:
            hints.append(self.renderer.background_hint)
        if compact:
            hints = [hint.partition(" · ")[0] for hint in hints]
        return " · ".join(hints)

    def _activity_text(self) -> FormattedText:
        width = max(1, self.app.output.get_size().columns)
        text = terminal_text(self._activity_hint())
        if get_cwidth(text) > width:
            text = terminal_text(self._activity_hint(compact=True))
        if get_cwidth(text) > width:
            text = text[: max(0, width - 1)] + "…"
        return FormattedText([("", text)])

    async def _refresh_activities(self) -> None:
        backend = self.backend
        thread_id = backend.thread_id if backend is not None else None
        total: int | None = 0
        if backend is not None and thread_id is not None:
            try:
                page = await backend.app.query_child_executions(parent_thread_id=thread_id, limit=1)
                total = page.total
            except Exception:
                # Failed inspection must not take down an active conversation.
                total = None
        if backend is self.backend and (backend is None or backend.thread_id == thread_id):
            self._activity_thread = thread_id
            self._subagent_total = total
            self.app.invalidate()

    async def _activity_refresher(self) -> None:
        while not self.closing:
            await self._refresh_activities()
            await asyncio.sleep(2)

    def _toolbar(self) -> FormattedText:
        return FormattedText([("", self.status.line(self.app.output.get_size().columns))])

    def _composer_header(self) -> FormattedText:
        label = "Answer required" if self.interaction else "Message"
        hint = "Enter to add guidance" if self.can_steer else "draft only · working" if self.busy else "Enter to send"
        if not self.ready:
            hint = "draft only · preparing"
        if self.selection or self.interaction:
            hint = "Enter to confirm · Esc to go back"
        width = max(1, self.app.output.get_size().columns)
        title = f" {label} "
        suffix = f" {hint} " if width >= 60 else ""
        if width < 60 and not (self.selection or self.interaction):
            mode = "Scroll" if self.mouse else "Select"
            if get_cwidth(title) + len(mode) + 2 <= width:
                suffix = f" {mode} "
        return FormattedText(
            [
                ("class:input-area.label", title),
                ("class:input-area.border", "─" * max(0, width - get_cwidth(title + suffix))),
                ("class:input-area.hint", suffix),
            ]
        )

    def _hints(self) -> str:
        size = self.app.output.get_size()
        if self.history_browser is not None:
            hints = ["Ctrl+T/q close", "↑↓/PgUp/PgDn scroll", "Home page top", "End latest", "Ctrl+O details"]
        elif self.resume_browser is not None:
            hints = (
                ["Enter save name", "Esc cancel name", "Empty name uses first input"]
                if self.resume_browser.renaming
                else [
                    "Enter resume",
                    "Esc back",
                    "Ctrl+A current directory" if self.resume_browser.all_directories else "Ctrl+A all directories",
                    "↑↓ select",
                    "Ctrl+T history",
                    "F2 rename",
                    "PgUp/PgDn pages",
                    "F5 refresh",
                ]
            )
        elif self.composer.buffer.complete_state is not None:
            hints = [
                "Enter complete",
                "Esc back" if self.interaction or self.menu_handler else "Esc dismiss",
                "↑↓ choose",
            ]
        elif self.selection or self.interaction:
            hints = ["Enter confirm", "Esc back"]
            if self.selection is not None:
                hints += ["Ctrl+Space focus", "Menu" if self.selector_focused else "Composer"]
                hints.append("↑↓ choose" if self.selector_focused and not self.composer.text else "↑↓ edit")
        else:
            # Submission semantics already live in the composer header. Keep history
            # first so it remains discoverable when secondary shortcuts cannot fit.
            action = "Ctrl+C cancel" if self.busy else "Ctrl+C twice exit"
            hints = [
                "Ctrl+T history",
                "Ctrl+O details",
                "F2 tasks",
                "/notes",
                "Esc select" if self.mouse else "Esc scroll",
            ]
            hints += ["Alt+Enter newline", action, "PgUp/PgDn scroll" if self.view.follow else "Ctrl+End latest"]
            if size.columns < 60:
                hints = [
                    "Ctrl+T history",
                    "/notes",
                    "Esc select" if self.mouse else "Esc scroll",
                    "/help",
                    "Alt+Enter newline",
                    action,
                ]

        # Wrap between whole shortcuts, not inside key names. Recompute both text
        # and Window height on every layout pass, including after a resize.
        rows = [""]
        max_rows = 2 if size.rows >= 12 else 1
        for hint in hints:
            if get_cwidth(" " + hint) > size.columns:
                continue
            candidate = rows[-1] + " · " + hint if rows[-1] else " " + hint
            if get_cwidth(candidate) <= size.columns:
                rows[-1] = candidate
            elif len(rows) < max_rows:
                rows.append(" " + hint)
            else:
                break
        return "\n".join(rows)

    def _save_draft(self) -> None:
        self.selector_focused = True
        if self._saved_draft is None:
            self._saved_draft = self.composer.buffer.document
            self._draft_generation += 1
        self.composer.buffer.reset()

    def _restore_draft(self) -> None:
        self.question_card = None
        self.selection = None
        self.app.layout.focus(self.composer)
        if self._saved_draft is not None:
            self.composer.buffer.document = self._saved_draft
            self._saved_draft = None
            self._draft_generation += 1
        if not self.busy and self.interaction is None:
            self.status.state = "ready"
        self.app.invalidate()

    async def _show_notes(self) -> None:
        if self.backend is None or self.backend.thread_id is None:
            self.emit("No saved notes in this session.")
            return
        await self._load_notes(force=True)

    async def _load_notes(self, *, force: bool = False) -> None:
        backend = self.backend
        if backend is None or backend.thread_id is None:
            self._notes_thread = None
            return
        thread_id = backend.thread_id
        page = await backend.app.thread_notes(thread_id=thread_id)
        if self.backend is backend and backend.thread_id == thread_id:
            self.renderer.restore_notes(page, force=force)
            self._notes_thread = thread_id

    async def _activate_decisions(self) -> None:
        if self.backend is None or self.closing or self.menu_handler is not None:
            return
        pending = await self.backend.interaction()
        if pending is None:
            if self.interaction is not None:
                self.interaction = None
                self._restore_draft()
            return
        if self.interaction is not None and self.interaction.batch == pending.batch:
            return
        self._save_draft()
        self.interaction = pending
        self.selection = pending.selection()
        self.status.state = "waiting for you"
        self._emit_decision()
        self.bell()

    def _emit_decision(self) -> None:
        from a13n_harness_ui.surfaces import StructuredQuestionRequestView

        assert self.interaction is not None
        self.selector_focused = self.selection is not None
        if isinstance(self.interaction.request, StructuredQuestionRequestView):
            self.renderer.register_questions(self.interaction.request)
            assert self.selection is not None
            self.question_card = QuestionCard(
                self.interaction,
                self.selection,
                submit=self._submit_question,
                cancel=self._cancel_question,
            )
            self.app.layout.focus(self.question_card.control)
            self.app.invalidate()
            return
        self.question_card = None
        self.app.layout.focus(self.composer)
        self.renderer.finish()
        self.renderer.transcript.append(
            terminal_text(self.interaction.display_prompt()), kind=self.interaction.prompt_kind
        )
        self.renderer.append(self.interaction.prompt() + "\n", display=False)
        self.app.invalidate()

    def _cancel_question(self) -> None:
        self.app.create_background_task(self.cancel())

    def _submit_question(self, text: str) -> None:
        if self.busy and not (text.startswith("/") and self.registry.lookup(text) is not None):
            if self.question_card is not None:
                self.question_card.error = "Wait for the current action to finish before confirming an answer."
            return
        if self._input_task is None or self._input_task.done():
            self._input_task = asyncio.create_task(self._answer_question(text))

    async def _answer_question(self, text: str) -> None:
        card = self.question_card
        if card is None:
            return
        if text.startswith("/") and self.registry.lookup(text) is not None:
            try:
                invocation = self.registry.parse(text, busy=self.busy)
                if invocation.command.name not in {
                    "help",
                    "mode",
                    "status",
                    "quit",
                    "cancel",
                    "theme",
                    "mouse",
                    "review",
                }:
                    raise ValueError("Finish this interaction or /cancel first. Your input is preserved.")
                self.emit(f"Command accepted: /{invocation.command.name}")
                await self.command(invocation)
            except Exception as exc:
                card.error = str(exc)
            return
        if text.startswith("/"):
            self.emit("No matching command; treating the original input as plain text.")
        await self.decision_answer(text)

    def bell(self) -> None:
        with suppress(OSError):
            self.app.output.bell()
            self.app.output.flush()

    @property
    def busy(self) -> bool:
        return self.job is not None and not self.job.done()

    @property
    def can_steer(self) -> bool:
        return (
            self.busy
            and self.job_kind == "run"
            and self.status.state != "cancelling"
            and self.backend is not None
            and self.backend.receipt_id is not None
        )

    def emit(self, text: str, *, kind: str = "notice") -> None:
        self.renderer.finish()
        self.renderer.append(text + "\n", kind=kind)
        self.app.invalidate()

    async def flush(self) -> None:
        self.renderer.transcript.detailed = self.status.mode == "detailed"
        self.renderer.drain()
        self.app.invalidate()

    async def _flusher(self) -> None:
        last_second = -1
        while not self.closing:
            size = max((block.size for block in self.renderer.transcript.blocks.values()), default=0)
            await asyncio.sleep(0.2 if size > 128 * 1024 else 0.1 if size > 32 * 1024 else 1 / 15)
            if (
                self.ready
                and not self.closing
                and not self.busy
                and (self._input_task is None or self._input_task.done())
                and self.interaction is not None
                and self.interaction.expired
            ):
                self.emit(f"Timed out: {self.interaction.request.tool_name}. No answer or approval was supplied.")
                self._finish_decision(self.interaction.expire())
            if self.renderer.transcript.dirty:
                await self.flush()
            if self.status.started is not None:
                second = int(time.monotonic() - self.status.started)
                if second != last_second:
                    last_second = second
                    self.app.invalidate()

    def _bindings(self) -> KeyBindingsBase:
        keys = KeyBindings()

        @keys.add("enter")
        def submit(event: KeyPressEvent) -> None:
            if event.current_buffer.complete_state is not None:
                state = event.current_buffer.complete_state
                selected = state.current_completion
                token = event.current_buffer.document.text_before_cursor.rsplit(" ", 1)[-1]
                exact = any(item.text == token for item in state.completions)
                if selected is not None or not exact:
                    if selected is None:
                        event.current_buffer.complete_next()
                    event.current_buffer.complete_state = None
                    return
                event.current_buffer.complete_state = None
            text = event.current_buffer.text
            steering_receipt: str | None = None
            if self.selection is not None and self.selector_focused and not text.strip():
                try:
                    text = self.selection.answer()
                except ValueError as exc:
                    self.emit(str(exc))
                    return
            try:
                if self.inline.tokens(text):
                    self.inline.compile(self.pastes.expand(text))
                    if text.startswith("!") or (text.startswith("/") and self.registry.lookup(text) is not None):
                        raise ValueError("Attachments belong to prompts, not commands. Your draft is preserved.")
                if text.startswith("/") and self.registry.lookup(text) is not None:
                    invocation = self.registry.parse(text, busy=self.busy)
                    name = invocation.command.name
                    local = {"help", "mode", "status", "quit", "cancel", "theme", "mouse"}
                    if not self.ready and name not in local:
                        raise ValueError("Still preparing. Your draft is preserved; /help is available.")
                    if (self.interaction or self.menu_handler) and name not in local | {"review"}:
                        raise ValueError("Finish this interaction or /cancel first. Your input is preserved.")
                    if self._input_task is not None and not self._input_task.done() and name not in local:
                        raise ValueError("Finishing the previous action. Your input is preserved.")
                elif text.startswith("!"):
                    self._validate_local_shell(text)
                elif (self._input_task is not None and not self._input_task.done()) or not self.ready:
                    raise ValueError("Finishing the previous action. Your draft is preserved.")
                elif not text.strip() and not self.images:
                    return
                elif self.busy:
                    if not self.can_steer:
                        raise ValueError(
                            "Still preparing or working. Your draft is preserved; /cancel stops active work."
                        )
                    if self.inline.tokens(text):
                        raise ValueError(
                            "Active-run guidance accepts text only. Text and attachments remain in your draft."
                        )
                    assert self.backend is not None
                    # Freeze at Enter, before scheduling: never retarget a later Run.
                    steering_receipt = self.backend.receipt_id
            except ValueError as exc:
                self.emit(str(exc))
                return
            self._submitted_draft = event.current_buffer.document
            text = self.pastes.expand(text)
            if self.interaction is None and self.menu_handler is None:
                # Plain history recalls labels as text, never resurrects attachments.
                event.current_buffer.text = self.inline.display(text)
                event.current_buffer.append_to_history()
            event.current_buffer.reset()
            # Reset clears undo history. Only saved/recoverable drafts can now
            # refer to folded payloads; edits and undo retain them until here.
            self.pastes.retain(
                tuple(
                    document.text
                    for document in (self._saved_draft, self._recoverable, self._submitted_draft, self._sending_draft)
                    if document is not None
                )
            )
            self.inline.retain(
                (
                    text,
                    *self.clipboard.texts,
                    *(
                        doc.text
                        for doc in (self._saved_draft, self._recoverable, self._submitted_draft, self._sending_draft)
                        if doc
                    ),
                )
            )
            if self._input_task is not None and not self._input_task.done():
                self.app.create_background_task(self.handle(text, steering_receipt=steering_receipt))
            else:
                self._input_task = asyncio.create_task(self.handle(text, steering_receipt=steering_receipt))

        selecting = Condition(lambda: self.selection is not None and self.selector_focused and not self.composer.text)

        @keys.add("up", filter=selecting)
        def select_previous(event: KeyPressEvent) -> None:
            assert self.selection is not None
            self.selection.move(-1)

        @keys.add("down", filter=selecting)
        def select_next(event: KeyPressEvent) -> None:
            assert self.selection is not None
            self.selection.move(1)

        @keys.add(" ", filter=selecting & Condition(lambda: self.selection is not None and self.selection.multiple))
        def select_multiple(event: KeyPressEvent) -> None:
            assert self.selection is not None
            self.selection.toggle()

        @keys.add("c-space")
        def focus_region(event: KeyPressEvent) -> None:
            if self.selection is not None:
                self.selector_focused = not self.selector_focused

        @keys.add(
            "escape",
            filter=Condition(
                lambda: (
                    self.interaction is None
                    and self.menu_handler is None
                    and (self._input_task is None or self._input_task.done())
                )
            ),
        )
        def mouse_mode(event: KeyPressEvent) -> None:
            if event.current_buffer.complete_state is not None:
                event.current_buffer.cancel_completion()
            else:
                self.mouse = not self.mouse

        @keys.add(
            "escape",
            filter=Condition(
                lambda: (
                    self.interaction is not None
                    or self.menu_handler is not None
                    or (self._input_task is not None and not self._input_task.done())
                )
            ),
        )
        def back(event: KeyPressEvent) -> None:
            if self.busy or (self._input_task is not None and not self._input_task.done()):
                event.app.create_background_task(self.cancel())
                return
            event.app.create_background_task(self.cancel())

        @keys.add("escape", "enter")
        def newline(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text("\n")

        @keys.add("c-c")
        def interrupt(event: KeyPressEvent) -> None:
            if (
                self.busy
                or self.interaction is not None
                or self.menu_handler is not None
                or (self._input_task is not None and not self._input_task.done())
            ):
                self._last_interrupt = float("-inf")
                event.app.create_background_task(self.cancel())
            else:
                now = time.monotonic()
                if now - self._last_interrupt <= 2:
                    self.status.state = "closing"
                    event.app.exit()
                    return
                event.current_buffer.reset()
                self._draft_generation += 1
                self._last_interrupt = now
                self.emit("Press Ctrl+C again within 2 seconds to exit. Draft cleared.")

        @keys.add("c-d")
        def eof(event: KeyPressEvent) -> None:
            if event.current_buffer.text:
                event.current_buffer.delete()
            elif self.images:
                self.emit("Files are still attached. /quit exits; Ctrl+C clears this draft.")
            else:
                event.app.exit()

        @keys.add("f2")
        def toggle_tasks(event: KeyPressEvent) -> None:
            self.renderer.tasks.expanded = not self.renderer.tasks.expanded
            event.app.invalidate()

        @keys.add(Keys.BracketedPaste)
        def paste_text(event: KeyPressEvent) -> None:
            pasted = self.inline.external_text(event.data)
            if self.interaction is None and self.menu_handler is None:
                pasted = self.pastes.insert(pasted)
            else:
                pasted = pasted.replace("\r\n", "\n").replace("\r", "\n")
            if event.current_buffer.selection_state is not None:
                event.current_buffer.cut_selection()
            event.current_buffer.insert_text(pasted)
            if len(event.data) > 1000:
                self.emit("Long paste folded. Alt+E expands it for editing; Backspace removes the block.")

        @keys.add("escape", "e")
        def expand_pastes(event: KeyPressEvent) -> None:
            text = self.pastes.expand(event.current_buffer.text)
            event.current_buffer.document = Document(text, len(text))

        @keys.add("backspace")
        def delete_before(event: KeyPressEvent) -> None:
            buffer = event.current_buffer
            if buffer.selection_state is not None:
                buffer.cut_selection()
                return
            buffer.delete_before_cursor(self.pastes.deletion(buffer.text, buffer.cursor_position, backward=True))

        @keys.add("delete")
        def delete_after(event: KeyPressEvent) -> None:
            buffer = event.current_buffer
            if buffer.selection_state is not None:
                buffer.cut_selection()
                return
            buffer.delete(self.pastes.deletion(buffer.text, buffer.cursor_position, backward=False))

        @keys.add("c-v")
        @keys.add("escape", "v")
        def paste_image(event: KeyPressEvent) -> None:
            self.start_clipboard()

        @keys.add("c-o")
        def toggle(event: KeyPressEvent) -> None:
            self.status.mode_explicit = True
            self.status.mode = "detailed" if self.status.mode == "concise" else "concise"
            self.renderer.transcript.detailed = self.status.mode == "detailed"
            self.renderer.transcript.dirty = True

        @keys.add("pageup")
        def page_up(event: KeyPressEvent) -> None:
            self.view.scroll(-max(1, self.view.height - 2))

        @keys.add("pagedown")
        def page_down(event: KeyPressEvent) -> None:
            self.view.scroll(max(1, self.view.height - 2))

        @keys.add("c-end")
        def latest(event: KeyPressEvent) -> None:
            self.view.latest()

        @keys.add("c-t")
        def history(event: KeyPressEvent) -> None:
            self.open_history()

        question_inspection_keys = KeyBindings()
        question_inspection_keys.add("c-t")(history)
        question_inspection_keys.add("c-o")(toggle)
        browser_keys = KeyBindings()

        @browser_keys.add("c-t")
        @browser_keys.add("q")
        @browser_keys.add("escape")
        @browser_keys.add("c-c")
        def close_history(event: KeyPressEvent) -> None:
            self.close_history()

        @browser_keys.add("up")
        @browser_keys.add("pageup")
        @browser_keys.add("down")
        @browser_keys.add("pagedown")
        def history_scroll(event: KeyPressEvent) -> None:
            browser = self.history_browser
            if browser is not None:
                key = event.key_sequence[-1].key
                amount = max(1, browser.view.height - 2) if key in {"pageup", "pagedown"} else 1
                event.app.create_background_task(browser.scroll(-amount if key in {"up", "pageup"} else amount))

        @browser_keys.add("home")
        def history_top(event: KeyPressEvent) -> None:
            if self.history_browser is not None:
                self.history_browser.view.scroll(-len(self.history_browser.view.transcript.rows))

        @browser_keys.add("end")
        def history_latest(event: KeyPressEvent) -> None:
            if self.history_browser is not None:
                event.app.create_background_task(self.history_browser.navigate())

        @browser_keys.add("c-o")
        def history_detail(event: KeyPressEvent) -> None:
            if self.history_browser is not None:
                transcript = self.history_browser.view.transcript
                transcript.detailed = not transcript.detailed
                transcript.dirty = True

        return merge_key_bindings(
            [
                ConditionalKeyBindings(
                    keys,
                    filter=Condition(
                        lambda: (
                            self.question_card is None and self.history_browser is None and self.resume_browser is None
                        )
                    ),
                ),
                ConditionalKeyBindings(browser_keys, filter=Condition(lambda: self.history_browser is not None)),
                ConditionalKeyBindings(
                    question_inspection_keys,
                    filter=Condition(lambda: self.question_card is not None and self.history_browser is None),
                ),
                ConditionalKeyBindings(
                    DynamicKeyBindings(lambda: self.question_card.bindings if self.question_card else KeyBindings()),
                    filter=Condition(lambda: self.question_card is not None and self.history_browser is None),
                ),
            ]
        )

    def open_resume(self) -> None:
        if self.backend is None or self.resume_browser is not None:
            return
        if self.busy or self.interaction is not None or self.menu_handler is not None:
            raise ValueError("Finish active work or /cancel before browsing sessions.")

        async def resume(thread_id: str) -> None:
            async def switch() -> str:
                result = await self._resume(thread_id)
                self.close_resume()
                return result

            def failed(exc: Exception) -> None:
                browser.message = f"{exc} · F5 refreshes"

            self.launch(switch(), discard_images=True, on_error=failed)
            if self.job is not None:
                await self.job

        browser = ResumeBrowser(self.backend, self.app, resume, self.close_resume, self.open_history)
        self.resume_browser = browser
        self.app.layout.update_parents_relations()
        self.app.layout.focus(browser.search)
        self.app.create_background_task(browser.initialize())
        self.app.invalidate()

    def close_resume(self) -> None:
        if self.resume_browser is not None:
            self.resume_browser.shutdown()
            self.resume_browser = None
            self.app.layout.focus(self.composer)
            self.app.invalidate()

    def open_history(self, thread_id: str | None = None) -> None:
        if self.history_browser is not None:
            return
        if self.backend is None:
            return
        thread_id = thread_id or self.backend.thread_id
        if thread_id is None:
            self.emit("No saved messages yet.")
            return
        backend = self.backend

        async def load(cursor: str | None, continuation_id: str | None):
            return await backend.app.get_thread_transcript(
                thread_id=thread_id,
                cursor=cursor,
                expected_continuation_id=continuation_id,
                limit=50,
            )

        browser = HistoryBrowser(self.status, load, self.app.invalidate)
        browser.renderer.transcript.theme = self.renderer.transcript.theme
        self.history_browser = browser
        self.history_window.content = browser.view
        self.app.layout.focus(browser.view)
        self.app.create_background_task(browser.navigate())
        self.app.invalidate()

    def close_history(self) -> None:
        if self.history_browser is not None:
            self.history_browser.close()
            self.history_browser = None
            if self.question_card is not None:
                self.app.layout.focus(
                    self.question_card.editor if self.question_card.editing else self.question_card.control
                )
            else:
                self.app.layout.focus(self.resume_browser.search if self.resume_browser else self.composer)
            self.app.invalidate()

    def _restore_resumed_history(self) -> None:
        assert self.backend is not None
        page = self.backend.resumed_transcript
        if page is None:
            return
        from .history import restore_transcript

        renderer = StreamRenderer(self.status)
        renderer.transcript.theme = resolve_theme(self.status.theme)
        renderer.transcript.detailed = self.status.mode == "detailed"
        try:
            restore_transcript(renderer, page)
        except BaseException:
            renderer.transcript.close()
            raise
        self.renderer.transcript.close()
        self.renderer = renderer
        self.view.transcript = renderer.transcript
        self.view.latest()
        self.backend.resumed_transcript = None

    async def _resume(self, selected: str) -> str:
        assert self.backend is not None
        result = await self.backend.resume(selected)
        self._restore_resumed_history()
        for notice in self.status.notices:
            self.emit(notice)
        self.status.notices.clear()
        return result

    async def run(self, backend: SessionBackend, *, terminal_task: asyncio.Task[None] | None = None) -> None:
        self.backend = backend
        self._restore_resumed_history()
        self.renderer.transcript.theme = resolve_theme(self.status.theme)
        self.renderer.transcript.dirty = True
        self.app.style = self._style()
        self.status.state = "preparing"
        self.emit(
            f"Harness UI · {self.directory}\n{self.status.agent} · /agent to switch · /help for commands\nEnter sends a message or guides active work. !command runs on this host."
        )
        if not local_sandbox_supported():
            self.emit(WINDOWS_EXECUTION_NOTICE)
        for notice in self.status.notices:
            self.emit(notice)
        self.status.notices.clear()
        await self._activate_decisions()
        self.registry.set_skills(await backend.skill_catalog())
        if backend.thread_id is not None:
            self.renderer.tasks.restore(await backend.app.thread_tasks(thread_id=backend.thread_id))
            await self._load_notes()
        self.ready = True
        self.status.state = "waiting for you" if self.interaction is not None else "ready"
        self.app.invalidate()
        flusher = self.app.create_background_task(self._flusher())
        activity_refresher = self.app.create_background_task(self._activity_refresher())
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()

        def terminal_failure(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            lost_task = (
                context.get("message") == "Task was destroyed but it is pending!"
                and context.get("exception") is None
                and isinstance(context.get("task"), asyncio.Task)
            )
            error = context.get("exception")
            if not isinstance(error, BaseException):
                error = RuntimeError(str(context.get("message", "Terminal background task failed")))
            if lost_task:
                message = pending_task_warning(error, session_id=self.status.session_id, context=context)
            else:
                message = exception_report(
                    error,
                    session_id=self.status.session_id,
                    phase="terminal event loop",
                    request=self.request,
                    directory=self.directory,
                    loop_context=context,
                )

            def present() -> None:
                if self.closing or not self.app.is_running or self.app.is_done:
                    return
                if lost_task:
                    self.emit(message)
                else:
                    self.app.exit(exception=RuntimeError(message))

            # GC may report a lost task from another thread, or during rendering.
            # Capture its locations now, but never re-enter the terminal renderer.
            loop.call_soon_threadsafe(present)

        loop.set_exception_handler(terminal_failure)
        try:
            with patch_stdout():
                if terminal_task is None:
                    await self.app.run_async(set_exception_handler=False)
                else:
                    await terminal_task
        except BaseException:
            # An App failure may leave its receipt unsettled. Stop the UI waiter
            # so cleanup can reach the App, which still owns the actual Run.
            if self.job is not None:
                self.job.cancel()
            raise
        finally:
            loop.set_exception_handler(previous_handler)
            self.closing = True
            self.close_history()
            self.close_resume()
            with CancelScope(shield=True):
                await self.cancel()
                if self._input_task is not None and not self._input_task.done():
                    self._input_task.cancel()
                    await asyncio.gather(self._input_task, return_exceptions=True)
                if self.job is not None:
                    with suppress(asyncio.CancelledError):
                        await self.job
                if self._clipboard_task is not None:
                    self._clipboard_task.cancel()
                    await asyncio.gather(self._clipboard_task, return_exceptions=True)
                flusher.cancel()
                activity_refresher.cancel()
                await asyncio.gather(flusher, activity_refresher, return_exceptions=True)
                self.renderer.finish()
                self.renderer.transcript.close()
                self.backend = None

    async def cancel(self) -> None:
        if self.interaction is not None and not self.busy and self.interaction.back():
            if self.interaction.expired:
                self._finish_decision(self.interaction.expire())
            else:
                self._finish_decision(None)
            return
        if (
            self._input_task is not None
            and self._input_task is not asyncio.current_task()
            and not self._input_task.done()
        ):
            self._input_task.cancel()
            await asyncio.gather(self._input_task, return_exceptions=True)
            self.emit("Action cancelled. Completed writes are not rolled back; preview current state before retry.")
        if self.backend is not None and not self.busy:
            await self.backend.cancel()
        if (self.interaction is not None or self.menu_handler is not None) and not self.busy:
            self.interaction = None
            self.menu_handler = None
            self._restore_draft()
            self.status.state = "ready"
            self.emit(
                "Interaction cancelled. Completed writes are retained; pending decisions remain unapproved. /status reopens decisions."
            )
            return
        if not self.busy:
            return
        self.status.state = "cancelling"
        self.app.invalidate()
        if self.job_kind == "run" and self.backend is not None:
            await self.backend.cancel()
        elif self.job is not None:
            self.job.cancel()

    def launch(
        self,
        operation: Coroutine[Any, Any, str],
        *,
        kind: str = "command",
        failure_input: str | None = None,
        discard_images: bool = False,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if self.busy:
            operation.close()
            self.emit("Still working. Use /cancel first.")
            return
        generation = self._draft_generation
        failure_draft = self.composer.buffer.document if self.inline.tokens(self.composer.text) else None
        self.job_kind = kind
        self.status.state = "working" if kind == "run" else kind
        self.status.started = time.monotonic()
        if kind == "run":
            self.renderer.assistant_seen = False
            self.renderer.gap = False

        async def execute() -> None:
            try:
                result = await operation
                if discard_images and self._draft_generation == generation:
                    buffer = self.composer.buffer
                    assert isinstance(buffer, AttachmentBuffer)
                    for token in self.inline.tokens(buffer.text):
                        buffer.replace_token(token, "")
                    # A confirmed conversation switch cannot undo attachments into
                    # a different Thread. Preserve any unrelated authored text.
                    buffer.reset(document=buffer.document)
                    self._draft_generation += 1
                if result:
                    self.emit(result)
            except asyncio.CancelledError:
                if kind != "local shell":
                    self.emit("Cancelled. Deferred requests remain unapproved.")
            except Exception as exc:
                from a13n_harness_ui.errors import HarnessUiError

                self.emit(
                    f"Error: {exc}"
                    if isinstance(exc, (ValueError, HarnessUiError))
                    else exception_report(
                        exc,
                        session_id=self.status.session_id,
                        phase=kind,
                        request=self.request,
                        directory=self.directory,
                    )
                )
                if on_error is not None:
                    on_error(exc)
                if failure_input is not None:
                    self._restore_rejected_command(failure_input, generation, draft=failure_draft)
            finally:
                self.renderer.finish()
                if self.status.started is not None:
                    self.status.elapsed = time.monotonic() - self.status.started
                self.status.started = None
                self.status.state = "ready"
                try:
                    await self._activate_decisions()
                    if self.backend is not None and not self.closing:
                        self.registry.set_skills(await self.backend.skill_catalog())
                        if self.backend.thread_id is not None:
                            self.renderer.tasks.restore(
                                await self.backend.app.thread_tasks(thread_id=self.backend.thread_id)
                            )
                            await self._load_notes()
                        else:
                            self.renderer.tasks.tasks.clear()
                except Exception as exc:
                    self.emit(f"Could not load pending decisions: {exc}. /status retries.")
                await self.flush()
                if kind == "run" and self.interaction is None and not self.closing:
                    self.bell()

        self.job = asyncio.create_task(execute())

    def _restore_rejected_command(self, text: str, generation: int, *, draft: Document | None = None) -> None:
        draft = draft or Document(text, len(text))
        if not self.composer.text and self._draft_generation == generation:
            self.composer.buffer.document = draft
        else:
            self._recoverable = draft
            self.emit("Input was not confirmed. /recover restores its draft; no automatic retry occurred.")

    def _validate_local_shell(self, text: str) -> None:
        validate_local_shell_support()
        if not text[1:].strip():
            raise ValueError("Usage: !command — run a command on this host, outside the model Sandbox.")
        if self.busy or self.interaction is not None or self.menu_handler is not None or not self.ready:
            raise ValueError(
                "Finish active work or /cancel before running a local shell command. Your draft is preserved."
            )
        if (
            self._input_task is not None
            and self._input_task is not asyncio.current_task()
            and not self._input_task.done()
        ):
            raise ValueError("Finishing the previous action. Your draft is preserved.")

    async def _local_shell(self, command: str) -> str:
        await run_local_shell(command, self.directory, self.renderer.local_shell)
        return ""

    async def handle(self, text: str, *, steering_receipt: str | None = None) -> None:
        slash_command = text.startswith("/") and self.registry.lookup(text) is not None
        if text.startswith("/") and not slash_command:
            self.emit("No matching command; treating the original input as plain text.")
        if text.startswith("!"):
            try:
                if self.inline.tokens(text):
                    raise ValueError("Attachments cannot be passed to a local shell command.")
                self._validate_local_shell(text)
            except ValueError as exc:
                self.emit(str(exc))
                self._restore_rejected_command(text, self._draft_generation)
                return
            self.launch(self._local_shell(text[1:]), kind="local shell", failure_input=text)
            return
        if steering_receipt is not None:
            self._draft_generation += 1
            generation = self._draft_generation
            try:
                assert self.backend is not None
                result = await self.backend.steer(
                    text, receipt_id=steering_receipt, skill_references=self.registry.skill_references(text)
                )
                self.emit(result)
            except asyncio.CancelledError:
                self._restore_rejected_command(text, generation)
                raise
            except Exception as exc:
                self.emit(str(exc))
                self._restore_rejected_command(text, generation)
            return
        if slash_command:
            generation = self._draft_generation
            try:
                invocation = self.registry.parse(text, busy=self.busy)
                self.emit(f"Command accepted: /{invocation.command.name}")
                await self.command(invocation)
            except Exception as exc:
                self.emit(str(exc))
                self._restore_rejected_command(text, generation)
            return
        if self.menu_handler is not None:
            await self.menu_answer(text)
        elif self.backend is None:
            self.emit("Still preparing. No prompt was sent.")
        elif self.interaction is not None:
            await self.decision_answer(text)
        elif text.strip() or self.images:
            self.send_prompt(text)

    @property
    def images(self) -> tuple[AttachmentUpload, ...]:
        return self.inline.uploads(self.composer.text)

    def insert_attachments(self, incoming: tuple[AttachmentUpload, ...]) -> None:
        add_images(self.images, incoming)
        buffer = self.composer.buffer
        assert isinstance(buffer, AttachmentBuffer)
        tokens = ""
        for upload in incoming:
            token = self.inline.reserve()
            self.inline.values[token].upload = upload
            tokens += token
        buffer.insert_attachment(tokens)

    def _begin_attachment(self) -> tuple[str, int] | None:
        if self.interaction is not None or self.selection is not None:
            self.emit("Attachments belong to conversation drafts. Finish or cancel this interaction first.")
            return None
        buffer = self.composer.buffer
        assert isinstance(buffer, AttachmentBuffer)
        try:
            if len(self.inline.tokens(buffer.text)) >= 8:
                raise ValueError("An input supports up to eight attachments.")
            token = self.inline.reserve()
            buffer.insert_attachment(token)
            return token, self._draft_generation
        except ValueError as exc:
            self.emit(str(exc))
            return None

    async def acquire_images(self, path: str | None = None, *, anchor: tuple[str, int] | None = None) -> None:
        anchor = anchor or self._begin_attachment()
        if anchor is None:
            return
        token, generation = anchor
        entry = self.inline.values[token]
        try:
            incoming = await asyncio.to_thread(
                lambda: (
                    (read_attachment((self.directory / Path(path).expanduser()).resolve()),)
                    if path
                    else clipboard_images()
                )
            )
            if generation != self._draft_generation or self.closing:
                raise ValueError("Image paste discarded because its draft changed. Paste again to attach here.")
            if token not in self.composer.text:
                entry.error = "Paste was removed before reading finished."
                return
            add_images(self.images, incoming)
            entry.upload = incoming[0]
            replacement = token
            for upload in incoming[1:]:
                extra = self.inline.reserve()
                self.inline.values[extra].upload = upload
                replacement += extra
            buffer = self.composer.buffer
            assert isinstance(buffer, AttachmentBuffer)
            buffer.replace_token(token, replacement)
        except asyncio.CancelledError:
            entry.error = "Image paste cancelled."
            raise
        except Exception as exc:
            entry.error = str(exc)
            self.emit(f"File not attached: {exc}")
        finally:
            self.app.invalidate()

    def start_clipboard(self) -> None:
        if self._clipboard_task is not None and not self._clipboard_task.done():
            self.emit("Still reading the clipboard.")
            return
        anchor = self._begin_attachment()
        if anchor is not None:
            self._clipboard_task = asyncio.create_task(self.acquire_images(anchor=anchor))

    def send_prompt(self, text: str) -> None:
        assert self.backend is not None
        if self.busy:
            draft = self._submitted_draft or Document(text, len(text))
            if not self.composer.text:
                self.composer.buffer.document = draft
            else:
                self._recoverable = draft
            self.emit("The active operation changed. Draft restored or available through /recover; send explicitly.")
            return
        source_id = f"input-{uuid4().hex}"
        try:
            prompt = self.inline.compile(text, source_id)
        except ValueError as exc:
            self.emit(str(exc))
            if not self.composer.text:
                self.composer.buffer.document = self._submitted_draft or Document(text, len(text))
            return
        draft = self._submitted_draft or Document(text, len(text))
        self._submitted_draft = None
        self._sending_draft = draft
        self._draft_generation += 1
        accepted = False

        def admitted() -> None:
            nonlocal accepted
            accepted = True
            self._sending_draft = None
            self._recoverable = None

        self.renderer.local_input(source_id, prompt.display_text)
        self.app.invalidate()
        execution = self.backend.execute(
            self.renderer,
            prompt=prompt,
            flush=self.flush,
            admitted=admitted,
            skill_references=self.registry.skill_references(prompt.text),
        )

        async def send() -> str:
            try:
                return await execution
            finally:
                if not accepted:
                    self.emit("Input was not admitted; draft restored or available through /recover.")
                    if not self.composer.text and not self.images and self._saved_draft is None:
                        self.composer.buffer.document = draft
                        self._draft_generation += 1
                    else:
                        self._recoverable = draft
                        self.emit("Prompt was not admitted. /recover restores it; no automatic retry occurred.")
                self._sending_draft = None

        self.view.latest()
        self.launch(send(), kind="run")

    def open_menu(
        self,
        title: str,
        choices: tuple[Choice, ...],
        handler: Callable[[str | tuple[str, ...]], Awaitable[None]],
        *,
        multiple: bool = False,
        explicit: bool = False,
    ) -> None:
        self._save_draft()
        self.menu_title = title
        self.status.state = "selecting"
        self.menu_handler = handler
        self.selection = Selection(choices, multiple=multiple, cursor=-1 if explicit else 0)
        self.emit(title)

    async def menu_answer(self, text: str) -> None:
        selection, handler = self.selection, self.menu_handler
        assert selection is not None and handler is not None
        try:
            value = resolve_choice(
                text, tuple(choice.value for choice in selection.choices), multiple=selection.multiple
            )
            values = (value,) if isinstance(value, str) else value
            if any(item not in {choice.value for choice in selection.choices} for item in values):
                raise ValueError("Choose one of the listed options, or /cancel.")
            self.menu_handler = None
            self.selection = None
            await handler(value)
            if self.menu_handler is None:
                self._restore_draft()
        except asyncio.CancelledError:
            self.menu_handler = None
            self._restore_draft()
            raise
        except Exception as exc:
            self.menu_handler, self.selection = handler, selection
            self.emit(f"Action failed: {exc}. Retry explicitly or /cancel; completed publications are not rolled back.")
            self.composer.buffer.document = Document(text, len(text))

    def offer_import(self) -> None:
        async def product(value: str | tuple[str, ...]) -> None:
            if value == "skip":
                self.emit("Import skipped. /import can migrate subagents later.")
                return
            assert isinstance(value, str)

            async def scope(selected: str | tuple[str, ...]) -> None:
                assert isinstance(selected, str) and self.backend is not None
                choices, preview = await self.backend.import_choices(value, selected)
                self.emit(preview)
                if not choices:
                    self.emit(
                        "No importable definitions in this scope. Existing files were not changed. /import tries another source."
                    )
                    return

                async def candidates(names: str | tuple[str, ...]) -> None:
                    chosen = (names,) if isinstance(names, str) else names

                    async def confirm(answer: str | tuple[str, ...]) -> None:
                        if answer == "yes":
                            assert self.backend is not None
                            self.emit(await self.backend.import_and_enroll(chosen))
                        else:
                            self.emit("Import skipped. Previously published configuration remains saved.")

                    self.open_menu(
                        "Import and enable "
                        + ", ".join(chosen)
                        + "? Model/tools inherit; definitions and Agent roster are separate no-clobber publications.",
                        (Choice("no", "Do not import"), Choice("yes", "Import and enable on the selected Agent")),
                        confirm,
                        explicit=True,
                    )

                self.open_menu(
                    "Select subagents to import (preview and diagnostics above)", choices, candidates, multiple=True
                )

            self.open_menu(
                f"Scan {value} definitions in which scope? Account stores are not imported.",
                (
                    Choice("project", "This project", str(self.directory)),
                    Choice("user", "User definitions", "Selected product's agents only"),
                ),
                scope,
            )

        self.open_menu(
            "Optional subagent migration",
            (
                Choice("skip", "Skip", "No external files scanned"),
                Choice("codex", "Codex", "Import definitions; inherit parent model and tools"),
                Choice("claude-code", "Claude Code", "Import definitions; inherit parent model and tools"),
            ),
            product,
        )

    async def decision_answer(self, text: str) -> None:
        if self.backend is None or self.interaction is None:
            return
        try:
            response = self.interaction.accept(text)
            if response == "review":
                self.launch(self.backend.review(self.interaction.request.request_id), kind="review")
            else:
                assert not isinstance(response, str)
                self._finish_decision(response)
        except (ValueError, TypeError) as exc:
            if self.question_card is not None:
                self.question_card.error = f"Answer not submitted: {exc}"
                self.question_card.top = len(self.question_card.rows(self.app.output.get_size().columns))
            else:
                self.emit(f"Answer not submitted: {exc}")
                self.composer.buffer.document = Document(text, len(text))

    def _finish_decision(self, response: ThreadDeferredResponse | None) -> None:
        assert self.backend is not None and self.interaction is not None
        if response is None:
            self.selection = self.interaction.selection()
            self.composer.text = ""
            self._emit_decision()
        else:
            self.interaction = None
            self._restore_draft()
            self.launch(self.backend.execute(self.renderer, response=response, flush=self.flush), kind="run")

    async def command(self, invocation: Invocation) -> None:
        name = invocation.command.name
        argument = invocation.arguments[0] if invocation.arguments else None
        if name == "ps":
            self.emit(self.renderer.process_details(), kind="processes")
        elif name == "subagents":
            if self.backend is None:
                self.emit("Subagent inspection is unavailable while preparing.")
            else:
                self.emit(await self.backend.subagents(argument), kind="subagents")
                await self._refresh_activities()
        elif name == "help":
            self.renderer.append(self.registry.help(argument) + "\n", markdown=True, kind="notice")
        elif name == "quit":
            self.closing = True
            if self.app.is_running:
                self.app.exit()
        elif name == "mode":
            self.status.mode_explicit = True
            self.renderer.finish()
            self.status.mode = argument or ("detailed" if self.status.mode == "concise" else "concise")
            self.emit(f"Display · {self.status.mode}")
            self.renderer.transcript.detailed = self.status.mode == "detailed"
            self.renderer.transcript.dirty = True
        elif name == "theme":
            if argument not in {None, "auto", "dark", "light"}:
                raise ValueError("Choose auto, dark, or light.")
            self.status.theme_explicit = True
            self.status.theme = "light" if argument == "light" else "dark" if argument == "dark" else "auto"
            self.renderer.transcript.theme = resolve_theme(self.status.theme)
            self.renderer.transcript.dirty = True
            self.app.style = self._style()
            self.emit(f"Theme · {self.status.theme}.")
        elif name == "mouse":
            self.mouse = argument == "on" if argument else not self.mouse
            self.emit(
                "Mouse wheel capture on; /mouse off restores native selection."
                if self.mouse
                else "Mouse capture off; select and copy text with your terminal."
            )
        elif name == "attach":
            await self.acquire_images(argument)
        elif name == "paste-image":
            self.start_clipboard()
        elif name == "recover":
            if self._recoverable is None:
                raise ValueError("No unsubmitted prompt to recover.")
            self.composer.buffer.document = self._recoverable
            self._recoverable = None
            self._draft_generation += 1
        elif name == "cancel":
            await self.cancel()
        elif name == "status":
            tokens = f"{self.status.context_tokens:,}" if self.status.context_tokens is not None else "unknown"
            window = f"{self.status.context_window:,}" if self.status.context_window else "unknown"
            self.emit(
                "Status · "
                + self.status.state.capitalize()
                + "\n"
                + f"Agent        {self.status.agent}\nModel        {self.status.model}\n"
                + f"Reasoning    {self.status.thinking}\nService tier {self.status.service_tier_text} (requested)\n"
                + f"Context      {tokens} / {window} tokens\n"
                + f"Environment  {self.status.environment}\nWorkspace    {self.directory}\n"
                + f"Session      {self.status.session_id or '(new)'}\nDisplay      {self.status.mode}",
                kind="info",
            )
            if self.backend is not None and not self.busy:
                if self.status.model.startswith("openai-codex:") and self.interaction is None:
                    from .usage import show_codex_usage

                    self.launch(show_codex_usage(self), kind="usage")
                else:
                    self.launch(self.backend.pending())
            elif self.status.model.startswith("openai-codex:"):
                self.emit("/usage subscription · available while idle.")
        elif self.backend is None:
            raise ValueError("The App is not ready. No operation was started.")
        elif self.interaction is not None and name != "review":
            raise ValueError(
                "Answer the selectable prompt or use /cancel before another command. Pending requests stay unapproved."
            )
        elif name == "usage":
            from .usage import choose_codex_reset, show_codex_usage, thread_usage_text

            if argument in {None, "details"}:
                if self.backend.thread_id is None:
                    self.emit("No session selected. Send a message or /resume.")
                    return
                view = await self.backend.app.thread_usage(thread_id=self.backend.thread_id)
                self.emit(thread_usage_text(view, details=argument == "details"), kind="info")
            else:
                if not self.status.model.startswith("openai-codex:"):
                    raise ValueError("Subscription usage is available for Codex Agents only.")
                if self.busy:
                    raise ValueError("Wait for active work before inspecting subscription limits or resetting.")
                self.launch(choose_codex_reset(self) if argument == "reset" else show_codex_usage(self), kind="usage")
        elif name == "steer":
            assert argument is not None
            result = await self.backend.steer(argument)
            self.emit(result)
        elif name == "resume" and argument is None:
            self.open_resume()
        elif name in {"agent", "model", "thinking", "environment"} and argument is None:
            choices = await self.backend.choices(name)
            if not choices:
                self.emit("No choices available.")
                return

            async def selected(value: str | tuple[str, ...]) -> None:
                assert isinstance(value, str)
                await self.command(self.registry.parse(f"/{name} {value}"))

            self.open_menu(f"Select {name}", choices, selected)
        elif name == "import":
            self.offer_import()
        elif name == "agent":
            self.launch(self.backend.agents(argument), failure_input=invocation.source)
        elif name == "model":
            assert argument is not None
            self.launch(self.backend.models(argument), failure_input=invocation.source)
        elif name == "thinking":
            self.launch(self.backend.thinking(argument), failure_input=invocation.source)
        elif name == "fast":
            self.launch(self.backend.fast(argument), failure_input=invocation.source)
        elif name == "environment":
            self.launch(self.backend.set_environment(argument), failure_input=invocation.source)
        elif name == "new":
            self.renderer.clear_process_observations()
            self.launch(self.backend.new(), failure_input=invocation.source, discard_images=True)
        elif name == "resume":
            assert argument is not None
            self.launch(self._resume(argument), failure_input=invocation.source, discard_images=True)
        elif name == "review":
            assert argument is not None
            self.launch(self.backend.review(argument), failure_input=invocation.source)
        elif name == "notes":
            await self._show_notes()
        elif name == "history":
            self.open_history()
        elif name == "config":
            self.emit(
                f"Configuration: {self.request.config_path or Path.home() / '.a13n-harness-ui/a13n-harness-ui.yaml'}\n/agent selects an agent; /model selects and remembers a model for this project (/model default clears it); /thinking adjusts reasoning for subsequent turns; /fast temporarily selects priority service (/fast reset restores Model configuration).\nLaunch flags override file defaults; no slash command silently rewrites model files.\nUse `a13n-harness-ui config show --format json` for accepted values and `a13n-harness-ui config validate` after editing."
            )
