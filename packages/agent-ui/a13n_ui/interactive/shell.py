"""One native full-terminal Application; AgentUiApp retains all authority."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Coroutine, Generator
from contextlib import AbstractAsyncContextManager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prompt_toolkit.application import Application
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import ConditionalContainer, Float, FloatContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea

from a13n_ui.environment_profiles import WINDOWS_EXECUTION_NOTICE, local_sandbox_supported

from .attachments import DraftImage, add_images, clipboard_images, read_image
from .commands import CommandRegistry, Invocation
from .rendering import Status, StreamRenderer, terminal_text
from .selection import Choice, Selection, resolve_choice
from .setup import SetupWizard
from .theme import prompt_toolkit_style_rules, resolve_theme
from .transcript import TranscriptControl

if TYPE_CHECKING:
    from a13n_ui.cli import CliRequest

    from .backend import SessionBackend
    from .decisions import DecisionInteraction


def _load_runtime() -> Callable[..., AbstractAsyncContextManager[SessionBackend]]:
    # Heavy imports run in a worker; the prompt event loop never performs them.
    from .runtime import open_session

    return open_session


class SlashCompleter(Completer):
    def __init__(self, registry: CommandRegistry) -> None:
        self.registry = registry

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Generator[Completion]:
        text = document.text_before_cursor
        prefix = text.rsplit(" ", 1)[-1]
        for value, help_text in self.registry.completions(text):
            yield Completion(value, start_position=-len(prefix), display_meta=help_text)


class CliShell:
    def __init__(
        self,
        request: CliRequest,
        *,
        directory: Path | None = None,
        runtime_loader: Callable[[], Callable[..., AbstractAsyncContextManager[SessionBackend]]] = _load_runtime,
    ) -> None:
        self.request = request
        self.directory = directory or Path.cwd()
        self.runtime_loader = runtime_loader
        self.registry = CommandRegistry()
        self.status = Status(mode=request.display or "concise", mode_explicit=request.display is not None)
        self.renderer = StreamRenderer(self.status)
        self.backend: SessionBackend | None = None
        self.ready = False
        self.closing = False
        self.job: asyncio.Task[None] | None = None
        self.job_kind: str | None = None
        self._input_task: asyncio.Task[None] | None = None
        self.wizard: SetupWizard | None = None
        self.interaction: DecisionInteraction | None = None
        self.selection: Selection | None = None
        self.menu_title = ""
        self.menu_handler: Callable[[str | tuple[str, ...]], Awaitable[None]] | None = None
        self._saved_draft: Document | None = None
        self._stop = asyncio.Event()
        self.mouse = False
        self.images: tuple[DraftImage, ...] = ()
        self._saved_images: tuple[DraftImage, ...] = ()
        self._draft_generation = 0
        self._recoverable: tuple[Document, tuple[DraftImage, ...]] | None = None
        self._clipboard_task: asyncio.Task[None] | None = None
        self.view = TranscriptControl(self.renderer.transcript)
        self.composer = TextArea(
            style="class:input-area",
            get_line_prefix=lambda line, wrap: FormattedText(
                [("class:input-area.prompt", " > ")]
                if line == 0 and wrap == 0
                else [("class:input-area.continuation", "   " if wrap else " · ")]
            ),
            multiline=True,
            wrap_lines=True,
            height=self._composer_height,
            completer=SlashCompleter(self.registry),
            complete_while_typing=False,
        )
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
                    and (self.wizard is not None or self.interaction is not None or self.selection is not None)
                )
            ),
        )
        layout = FloatContainer(
            HSplit(
                [
                    self.output_window,
                    panel,
                    ConditionalContainer(
                        Window(FormattedTextControl(self._toolbar), height=1, style="class:status-bar"),
                        filter=Condition(lambda: self.status.show_status and self.app.output.get_size().rows >= 8),
                    ),
                    ConditionalContainer(
                        Window(
                            FormattedTextControl(self._attachment_chips), height=1, style="class:session-selector.key"
                        ),
                        filter=Condition(lambda: bool(self.images) and self.app.output.get_size().rows >= 10),
                    ),
                    ConditionalContainer(
                        Window(FormattedTextControl(self._composer_header), height=1, style="class:input-area.border"),
                        filter=Condition(lambda: self.app.output.get_size().rows >= 8),
                    ),
                    self.composer,
                    ConditionalContainer(
                        Window(FormattedTextControl(self._hints), height=1, style="class:session-selector.hint"),
                        filter=Condition(lambda: self.app.output.get_size().rows >= 12),
                    ),
                ]
            ),
            floats=[Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=8, scroll_offset=1))],
        )
        self.app: Application[None] = Application(
            layout=Layout(layout, focused_element=self.composer),
            key_bindings=self._bindings(),
            full_screen=True,
            mouse_support=Condition(lambda: self.mouse),
            style=self._style(),
            min_redraw_interval=1 / 15,
        )

    def _style(self) -> Style:
        rules = prompt_toolkit_style_rules(self.renderer.transcript.theme)
        rules.update(
            {
                "selection.focus": rules["session-selector.selection"],
                "selection.description": rules["session-selector.hint"],
                "selection.hint": rules["session-selector.key"],
                "warning": rules["status-bar.warning"],
            }
        )
        return Style.from_dict(rules)

    def _composer_height(self) -> Dimension:
        size = self.app.output.get_size()
        width = max(1, size.columns - 3)
        rows = sum(max(1, (get_cwidth(line) + width - 1) // width) for line in self.composer.text.split("\n"))
        minimum = 3 if size.rows >= 16 else 1
        height = min(max(1, size.rows // 3), max(minimum, min(7, rows)))
        return Dimension(min=1, preferred=height, max=height)

    def _panel_height(self) -> Dimension:
        available = max(1, self.app.output.get_size().rows // 2)
        choices = min(8, len(self.selection.choices)) + 1 if self.selection else 0
        height = min(available, choices + 1 if self.selection else 4)
        return Dimension(min=1, preferred=height, max=height)

    def _panel(self) -> FormattedText:
        prompt = (
            self.wizard.prompt()
            if self.wizard
            else self.interaction.prompt()
            if self.interaction
            else self.menu_title or "Choose an option"
        )
        # Full context remains scrollable above. Keep the decision and composer
        # usable on short terminals rather than allowing a panel to own the screen.
        fragments = [("class:session-selector.title", terminal_text(prompt.split("\n")[0]) + "\n")]
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
        return FormattedText(clipped)

    def _toolbar(self) -> FormattedText:
        return FormattedText([("", self.status.line(self.app.output.get_size().columns))])

    def _composer_header(self) -> FormattedText:
        label = "Setup" if self.wizard else "Answer required" if self.interaction else "Message"
        hint = "draft only · preparing" if not self.ready else "draft only · running" if self.busy else "Enter to send"
        if self.wizard or self.selection or self.interaction:
            hint = "Enter to confirm · Esc to go back"
        width = max(1, self.app.output.get_size().columns)
        title = f" {label} "
        suffix = f" {hint} " if width >= 60 else ""
        return FormattedText(
            [
                ("class:input-area.label", title),
                ("class:input-area.border", "─" * max(0, width - get_cwidth(title + suffix))),
                ("class:input-area.hint", suffix),
            ]
        )

    def _hints(self) -> str:
        width = self.app.output.get_size().columns
        if self.wizard or self.selection or self.interaction:
            return " ↑↓ choose · Enter confirm · Esc back · /cancel" if width >= 60 else " Enter confirm · Esc back"
        if width < 60:
            return " Enter send · Alt+Enter newline · /help"
        action = "Ctrl+C cancel" if self.busy else "Ctrl+C clear"
        follow = "PgUp/PgDn scroll" if self.view.follow else "Ctrl+End latest output"
        history = " · /history: earlier output" if self.renderer.transcript.evicted else ""
        return f" Enter send · Alt+Enter newline · {action} · {follow} · /help{history}"

    def _save_draft(self) -> None:
        if self._saved_draft is None:
            self._saved_draft = self.composer.buffer.document
            self._saved_images = self.images
            self.images = ()
            self._draft_generation += 1
        self.composer.buffer.reset()

    def _restore_draft(self) -> None:
        self.selection = None
        if self._saved_draft is not None:
            self.composer.buffer.document = self._saved_draft
            self._saved_draft = None
            self.images = self._saved_images
            self._saved_images = ()
            self._draft_generation += 1
        if not self.busy and self.wizard is None and self.interaction is None:
            self.status.state = "ready"
        self.app.invalidate()

    def start_setup(self) -> None:
        self._save_draft()
        self.wizard = SetupWizard()
        self.selection = self.wizard.selection_prompt()
        self.status.state = "setup"
        self.emit(self.wizard.prompt())

    async def _activate_decisions(self) -> None:
        if self.backend is None or self.closing or self.wizard is not None or self.menu_handler is not None:
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
        self.emit(pending.prompt())

    @property
    def busy(self) -> bool:
        return self.job is not None and not self.job.done()

    def emit(self, text: str) -> None:
        self.renderer.finish()
        self.renderer.append(text + "\n")
        self.app.invalidate()

    async def flush(self) -> None:
        self.renderer.drain()
        self.app.invalidate()

    async def _flusher(self) -> None:
        last_second = -1
        while not self.closing:
            size = max((len(block.source) for block in self.renderer.transcript.blocks.values()), default=0)
            await asyncio.sleep(0.2 if size > 128 * 1024 else 0.1 if size > 32 * 1024 else 1 / 15)
            if self.renderer.transcript.dirty:
                await self.flush()
            if self.status.started is not None:
                second = int(time.monotonic() - self.status.started)
                if second != last_second:
                    last_second = second
                    self.app.invalidate()

    def _bindings(self) -> KeyBindings:
        keys = KeyBindings()

        @keys.add("enter")
        def submit(event: KeyPressEvent) -> None:
            if event.current_buffer.complete_state is not None:
                event.current_buffer.complete_state = None
                return
            text = event.current_buffer.text
            if self.selection is not None and not text.strip():
                try:
                    text = self.selection.answer()
                except ValueError as exc:
                    self.emit(str(exc))
                    return
            try:
                if text.startswith("/"):
                    invocation = self.registry.parse(text, busy=self.busy)
                    name = invocation.command.name
                    local = {"help", "mode", "status", "quit", "cancel", "theme", "mouse"}
                    if not self.ready and name not in local:
                        raise ValueError("Still preparing. Your draft is preserved; /help is available.")
                    if (self.wizard or self.interaction or self.menu_handler) and name not in local | {"review"}:
                        raise ValueError("Finish this interaction or /cancel first. Your input is preserved.")
                    if self._input_task is not None and not self._input_task.done() and name not in local:
                        raise ValueError("Finishing the previous action. Your input is preserved.")
                elif self.busy or (self._input_task is not None and not self._input_task.done()) or not self.ready:
                    raise ValueError("Still preparing or working. Your draft is preserved; /cancel stops active work.")
                elif not text.strip() and not self.wizard and not self.images:
                    return
            except ValueError as exc:
                self.emit(str(exc))
                return
            if self.wizard is None and self.interaction is None and self.menu_handler is None:
                event.current_buffer.append_to_history()
            event.current_buffer.reset()
            if self._input_task is not None and not self._input_task.done():
                self.app.create_background_task(self.handle(text))
            else:
                self._input_task = asyncio.create_task(self.handle(text))

        selecting = Condition(lambda: self.selection is not None and not self.composer.text)

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

        @keys.add(
            "escape",
            filter=Condition(
                lambda: (
                    self.wizard is not None
                    or self.interaction is not None
                    or self.menu_handler is not None
                    or (self._input_task is not None and not self._input_task.done())
                )
            ),
        )
        def back(event: KeyPressEvent) -> None:
            if self.busy or (self._input_task is not None and not self._input_task.done()):
                event.app.create_background_task(self.cancel())
                return
            if self.wizard is not None and self.wizard.back():
                self.selection = self.wizard.selection_prompt()
                event.current_buffer.reset()
                self.emit(self.wizard.prompt())
            else:
                event.app.create_background_task(self.cancel())

        @keys.add("escape", "enter")
        def newline(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text("\n")

        @keys.add("c-c")
        def interrupt(event: KeyPressEvent) -> None:
            if (
                self.busy
                or self.wizard is not None
                or self.interaction is not None
                or self.menu_handler is not None
                or (self._input_task is not None and not self._input_task.done())
            ):
                event.app.create_background_task(self.cancel())
            else:
                event.current_buffer.reset()
                self.images = ()
                self._draft_generation += 1

        @keys.add("c-d")
        def eof(event: KeyPressEvent) -> None:
            if event.current_buffer.text:
                event.current_buffer.delete()
            elif self.images:
                self.emit("Images are still attached. /quit exits; Ctrl+C clears this draft.")
            else:
                event.app.exit()

        @keys.add("c-v")
        @keys.add("escape", "v")
        def paste_image(event: KeyPressEvent) -> None:
            self.start_clipboard()

        @keys.add("c-o")
        def toggle(event: KeyPressEvent) -> None:
            self.status.mode_explicit = True
            self.status.mode = "detailed" if self.status.mode == "concise" else "concise"

        @keys.add("pageup")
        def page_up(event: KeyPressEvent) -> None:
            self.view.scroll(-max(1, self.view.height - 2))

        @keys.add("pagedown")
        def page_down(event: KeyPressEvent) -> None:
            self.view.scroll(max(1, self.view.height - 2))

        @keys.add("c-end")
        def latest(event: KeyPressEvent) -> None:
            self.view.latest()

        return keys

    async def _lifetime(self) -> None:
        try:
            factory = await asyncio.to_thread(self.runtime_loader)
            if self.closing:
                return
            async with factory(self.request, self.directory, self.status, self.emit) as backend:
                self.backend = backend
                configured = await backend.initialize()
                self.renderer.transcript.theme = resolve_theme(self.status.theme)
                self.renderer.transcript.dirty = True
                self.app.style = self._style()
                self.ready = True
                self.status.state = "ready" if configured else "setup needed"
                if self.request.command == "setup" or not configured:
                    self.start_setup()
                elif self.request.thread_id:
                    self.emit(f"Resumed {self.request.thread_id}. /history shows saved messages.")
                    await self._activate_decisions()
                else:
                    self.emit(f"Ready · {self.status.model} · {self.status.environment}")
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.ready = False
            self.status.state = "startup failed"
            self.emit(
                f"Startup failed: {exc}\nUse `a13n-ui config validate` or `a13n-ui doctor` to diagnose. /quit exits."
            )
        finally:
            self.backend = None

    async def run(self) -> None:
        self.emit(
            f"Agent CLI · {self.directory}\n/help for commands · Alt+Enter newline · Ctrl+O detail · PageUp scroll\nPreparing locally; you can type now. No model request is made until you send a prompt."
        )
        if not local_sandbox_supported():
            self.emit(WINDOWS_EXECUTION_NOTICE)
        lifetime = asyncio.create_task(self._lifetime())
        flusher = asyncio.create_task(self._flusher())
        try:
            with patch_stdout():
                await self.app.run_async()
        finally:
            self.closing = True
            await self.cancel()
            if self._input_task is not None and not self._input_task.done():
                self._input_task.cancel()
                await asyncio.gather(self._input_task, return_exceptions=True)
            if self.job is not None:
                with suppress(asyncio.CancelledError):
                    await self.job
            self._stop.set()
            await lifetime
            if self._clipboard_task is not None:
                self._clipboard_task.cancel()
                await asyncio.gather(self._clipboard_task, return_exceptions=True)
            flusher.cancel()
            with suppress(asyncio.CancelledError):
                await flusher
            self.renderer.finish()

    async def cancel(self) -> None:
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
        if (self.wizard is not None or self.interaction is not None or self.menu_handler is not None) and not self.busy:
            was_setup = self.wizard is not None
            self.wizard = None
            self.interaction = None
            self.menu_handler = None
            self._restore_draft()
            self.status.state = "ready"
            self.emit(
                "Setup cancelled. No new publication requested."
                if was_setup
                else "Interaction cancelled. Completed writes are retained; pending decisions remain unapproved. /status reopens decisions."
            )
            return
        if not self.busy:
            return
        self.status.state = "cancelling"
        if self.job_kind == "run" and self.backend is not None:
            await self.backend.cancel()
        elif self.job is not None:
            self.job.cancel()
        self.emit("Cancellation requested; waiting for cleanup.")

    def launch(
        self,
        operation: Coroutine[Any, Any, str],
        *,
        kind: str = "command",
        failure_input: str | None = None,
        discard_images: bool = False,
    ) -> None:
        if self.busy:
            operation.close()
            self.emit("Still working. Use /cancel first.")
            return
        generation, images = self._draft_generation, self.images
        self.job_kind = kind
        self.status.state = "working" if kind == "run" else kind
        self.status.started = time.monotonic()
        if kind == "run":
            self.renderer.assistant_seen = False
            self.renderer.gap = False

        async def execute() -> None:
            try:
                result = await operation
                if discard_images and self._draft_generation == generation and self.images == images:
                    self.images = ()
                    self._draft_generation += 1
                if result:
                    self.emit(result)
            except asyncio.CancelledError:
                self.emit("Cancelled. Deferred requests remain unapproved.")
            except Exception as exc:
                self.emit(f"Error: {exc}")
                if failure_input is not None:
                    self._restore_rejected_command(failure_input, generation, images)
            finally:
                self.renderer.finish()
                if self.status.started is not None:
                    self.status.elapsed = time.monotonic() - self.status.started
                self.status.started = None
                self.status.state = "ready"
                try:
                    await self._activate_decisions()
                except Exception as exc:
                    self.emit(f"Could not load pending decisions: {exc}. /status retries.")
                await self.flush()

        self.job = asyncio.create_task(execute())

    def _restore_rejected_command(self, text: str, generation: int, images: tuple[DraftImage, ...]) -> None:
        draft = Document(text, len(text))
        if not self.composer.text and self._draft_generation == generation:
            self.composer.buffer.document = draft
        else:
            self._recoverable = (draft, images)
            self.emit("Command rejected. /recover restores its draft; no automatic retry occurred.")

    async def handle(self, text: str) -> None:
        if text.startswith("/"):
            generation, images = self._draft_generation, self.images
            try:
                invocation = self.registry.parse(text, busy=self.busy)
                await self.command(invocation)
            except Exception as exc:
                self.emit(str(exc))
                self._restore_rejected_command(text, generation, images)
            return
        if self.menu_handler is not None:
            await self.menu_answer(text)
        elif self.backend is None:
            self.emit("Still preparing. No prompt was sent.")
        elif self.wizard is not None:
            await self.setup_answer(text)
        elif self.interaction is not None:
            await self.decision_answer(text)
        elif text.strip() or self.images:
            self.send_prompt(text)

    def _attachment_chips(self) -> str:
        return terminal_text(
            " ".join(
                f"[{index}: {image.name} {len(image.data) // 1024} KiB]" for index, image in enumerate(self.images, 1)
            )
            + " · /remove index|all"
        )

    async def acquire_images(self, path: str | None = None) -> None:
        if self.wizard is not None or self.interaction is not None or self.selection is not None:
            self.emit("Images belong to conversation drafts. Finish or cancel this interaction first.")
            return
        generation = self._draft_generation
        try:
            incoming = await asyncio.to_thread(
                lambda: (
                    (read_image((self.directory / Path(path).expanduser()).resolve()),) if path else clipboard_images()
                )
            )
            if generation != self._draft_generation or self.closing:
                self.emit("Image paste discarded because its draft changed. Paste again to attach here.")
                return
            self.images = add_images(self.images, incoming)
            self.app.invalidate()
        except Exception as exc:
            self.emit(f"Image not attached: {exc}")

    def start_clipboard(self) -> None:
        if self._clipboard_task is not None and not self._clipboard_task.done():
            self.emit("Still reading the clipboard.")
            return
        self._clipboard_task = asyncio.create_task(self.acquire_images())

    def send_prompt(self, text: str) -> None:
        from pydantic_ai import BinaryContent

        assert self.backend is not None
        if self.busy:
            self.emit("Still working. Your draft is preserved; /steer sends additional text guidance.")
            return
        images, self.images = self.images, ()
        self._draft_generation += 1
        accepted = False
        draft = Document(text, len(text))

        def admitted() -> None:
            nonlocal accepted
            accepted = True
            self._recoverable = None

        prompt = (
            (text, *(BinaryContent(data=image.data, media_type=image.media_type) for image in images))
            if images
            else text
        )
        execution = self.backend.execute(self.renderer, prompt=prompt, flush=self.flush, admitted=admitted)

        async def send() -> str:
            try:
                return await execution
            finally:
                if not accepted:
                    if not self.composer.text and not self.images and self._saved_draft is None:
                        self.composer.buffer.document = draft
                        self.images = images
                        self._draft_generation += 1
                    else:
                        self._recoverable = (draft, images)
                        self.emit("Prompt was not admitted. /recover restores it; no automatic retry occurred.")

        self.renderer.append("You: " + (text or "(images)") + (f" [{len(images)} image(s)]" if images else ""))
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

    def offer_login(self, provider: str) -> None:
        async def choose(value: str | tuple[str, ...]) -> None:
            if value == "login":

                async def login() -> str:
                    assert self.backend is not None
                    try:
                        account = await self.backend.app.login_model_account(provider)
                        return f"Account: {account.provider.value} · {account.availability.value} · action: {account.required_action.value}"
                    finally:
                        if not self.closing:
                            self.offer_import()

                self.launch(login(), kind="login")
            else:
                self.offer_import()

        self.open_menu(
            "Configuration saved. Authenticate now? Existing account credentials are not imported.",
            (
                Choice("later", "Not now", "Use an existing account or /login later"),
                Choice("login", "Sign in", "Explicit device authorization"),
            ),
            choose,
        )

    def offer_import(self) -> None:
        async def product(value: str | tuple[str, ...]) -> None:
            if value == "skip":
                self.emit("Setup complete. /import can migrate subagents later.")
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
            "Step 3/3 · Optional subagent migration",
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
            elif response is None:
                self.selection = self.interaction.selection()
                self.emit(self.interaction.prompt())
            else:
                assert not isinstance(response, str)
                self.interaction = None
                self._restore_draft()
                self.launch(self.backend.execute(self.renderer, response=response, flush=self.flush), kind="run")
        except (ValueError, TypeError) as exc:
            self.emit(f"Answer not submitted: {exc}")
            self.composer.buffer.document = Document(text, len(text))

    async def setup_answer(self, text: str) -> None:
        if self.backend is None or self.wizard is None:
            return
        try:
            if self.wizard.question is not None:
                self.wizard.accept(text)
                if self.wizard.question is None:
                    self.emit(await self.backend.preview_setup(self.wizard))
                self.selection = self.wizard.selection_prompt()
                self.emit(self.wizard.prompt())
            elif resolve_choice(text, ("no", "yes")) == "yes":
                self.emit(await self.backend.publish_setup(self.wizard))
                values = self.wizard.values.copy()
                self.wizard = None
                if values.get("access") == "byos":
                    self.offer_login(values.get("provider", "codex"))
                else:
                    self._restore_draft()
            else:
                self.emit(self.wizard.prompt())
        except Exception as exc:
            self.emit(f"Setup failed: {exc}\n/cancel leaves setup. Existing files are preserved.")
            self.composer.buffer.document = Document(text, len(text))

    async def command(self, invocation: Invocation) -> None:
        name = invocation.command.name
        argument = invocation.arguments[0] if invocation.arguments else None
        if name == "help":
            self.emit(self.registry.help(argument))
        elif name == "quit":
            self.closing = True
            if self.app.is_running:
                self.app.exit()
        elif name == "mode":
            self.status.mode_explicit = True
            self.renderer.finish()
            self.status.mode = argument or ("detailed" if self.status.mode == "concise" else "concise")
            self.emit(
                f"Display: {self.status.mode}. Applies to subsequent stream events; use /history to review saved details."
            )
        elif name == "theme":
            if argument not in {None, "auto", "dark", "light"}:
                raise ValueError("Choose auto, dark, or light.")
            self.status.theme_explicit = True
            self.status.theme = "light" if argument == "light" else "dark" if argument == "dark" else "auto"
            self.renderer.transcript.theme = resolve_theme(self.status.theme)
            self.renderer.transcript.dirty = True
            self.app.style = self._style()
            self.emit(f"Theme: {self.renderer.transcript.theme.variant} ({self.renderer.transcript.theme.source}).")
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
        elif name == "remove":
            if argument == "all":
                self.images = ()
            elif argument is not None and argument.isdecimal() and 1 <= int(argument) <= len(self.images):
                index = int(argument) - 1
                self.images = self.images[:index] + self.images[index + 1 :]
            else:
                raise ValueError("Use /remove <image number> or /remove all.")
            self._draft_generation += 1
        elif name == "recover":
            if self._recoverable is None:
                raise ValueError("No unsubmitted prompt to recover.")
            self.composer.buffer.document, self.images = self._recoverable
            self._recoverable = None
            self._draft_generation += 1
        elif name == "cancel":
            await self.cancel()
        elif name == "status":
            self.emit(
                self.status.line()
                + f"\nWorkspace: {self.directory}\nSession: {self.status.session_id or '(new)'}\nEnvironment: {self.status.environment}\nContext is the last reported request footprint, not accumulated usage or an estimate of the next prompt."
            )
            if self.backend is not None and not self.busy:
                self.launch(self.backend.pending())
        elif self.backend is None:
            raise ValueError("The App is not ready. No operation was started.")
        elif self.wizard is not None:
            raise ValueError("Finish setup or use /cancel before another command.")
        elif self.interaction is not None and name != "review":
            raise ValueError(
                "Answer the selectable prompt or use /cancel before another command. Pending requests stay unapproved."
            )
        elif name == "steer":
            assert argument is not None
            self.emit(await self.backend.steer(argument))
        elif name == "setup":
            self.start_setup()
        elif name in {"model", "thinking", "environment", "resume"} and argument is None:
            choices = await self.backend.choices(name)
            if not choices:
                self.emit("No choices available. /setup configures a model; /new starts a session.")
                return

            async def selected(value: str | tuple[str, ...]) -> None:
                assert isinstance(value, str)
                await self.command(self.registry.parse(f"/{name} {value}"))

            self.open_menu(f"Select {name}", choices, selected)
        elif name == "import":
            self.offer_import()
        elif name == "model":
            self.launch(self.backend.models(argument), failure_input=invocation.source)
        elif name == "thinking":
            self.launch(self.backend.thinking(argument), failure_input=invocation.source)
        elif name == "environment":
            self.launch(self.backend.set_environment(argument), failure_input=invocation.source)
        elif name == "new":
            self.launch(self.backend.new(), failure_input=invocation.source, discard_images=True)
        elif name == "resume":
            self.launch(self.backend.resume(argument), failure_input=invocation.source, discard_images=True)
        elif name == "review":
            assert argument is not None
            self.launch(self.backend.review(argument), failure_input=invocation.source)
        elif name == "history":
            self.launch(self.backend.history(argument), failure_input=invocation.source)
        elif name == "login":

            async def login() -> str:
                assert self.backend is not None
                account = await self.backend.app.login_model_account(argument or "codex")
                return f"Account: {account.provider.value} · {account.availability.value} · action: {account.required_action.value}"

            self.launch(login(), kind="login", failure_input=invocation.source)
        elif name == "config":
            self.emit(
                f"Configuration: {self.request.config_path or Path.home() / '.a13n-ui/a13n-ui.yaml'}\nSession /model and /thinking override configured model settings for subsequent turns only.\nLaunch flags override file defaults; no slash command silently rewrites model files.\nUse `a13n-ui config show --format json` for accepted values and `a13n-ui config validate` after editing."
            )
        elif name in {"approve", "deny", "result"}:
            assert argument is not None
            response = await self.backend.decide(
                name, argument, invocation.arguments[1] if len(invocation.arguments) > 1 else None
            )
            if response is None:
                self.emit("Decision recorded locally. Answer the remaining requests to submit the complete batch.")
            else:
                self.launch(self.backend.execute(self.renderer, response=response, flush=self.flush), kind="run")
