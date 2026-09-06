"""Single-owner inline terminal with an immediately editable startup draft."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Callable, Coroutine, Generator
from contextlib import AbstractAsyncContextManager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style

from .commands import CommandRegistry, Invocation
from .rendering import Status, StreamRenderer
from .setup import SetupWizard

if TYPE_CHECKING:
    from a13n_ui.cli import CliRequest

    from .backend import SessionBackend


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


class InlineShell:
    def __init__(
        self,
        request: CliRequest,
        *,
        directory: Path | None = None,
        runtime_loader: Callable[[], Callable[..., AbstractAsyncContextManager[SessionBackend]]] = _load_runtime,
    ) -> None:
        self.request = request
        self._terminal_output = sys.stdout
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
        self.wizard: SetupWizard | None = None
        self._stop = asyncio.Event()
        self._flush_lock = asyncio.Lock()
        self.session: PromptSession[str] = PromptSession(
            message=lambda: "setup> " if self.wizard is not None else "> ",
            multiline=True,
            key_bindings=self._bindings(),
            completer=SlashCompleter(self.registry),
            complete_while_typing=False,
            bottom_toolbar=lambda: self.status.line() if self.status.show_status else None,
            reserve_space_for_menu=0,
            style=Style.from_dict({"bottom-toolbar": "bg:ansiblack ansibrightblack"}),
        )
        self.session.app.min_redraw_interval = 0.04

    @property
    def busy(self) -> bool:
        return self.job is not None and not self.job.done()

    def emit(self, text: str) -> None:
        self.renderer.finish()
        self.renderer.append(text + "\n")
        self.session.app.invalidate()

    async def flush(self) -> None:
        async with self._flush_lock:
            text = self.renderer.drain()
            if not text:
                return

            def write() -> None:
                self._terminal_output.write(text)
                self._terminal_output.flush()

            pending = asyncio.ensure_future(run_in_terminal(write))
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                # Once drained, bytes belong to this write. Wait for completion
                # even if its stream consumer has reached a terminal boundary.
                await asyncio.shield(pending)
                raise

    async def _flusher(self) -> None:
        last_second = -1
        while not self.closing:
            await asyncio.sleep(0.04)
            await self.flush()
            if self.status.started is not None:
                second = int(time.monotonic() - self.status.started)
                if second != last_second:
                    last_second = second
                    self.session.app.invalidate()

    def _bindings(self) -> KeyBindings:
        keys = KeyBindings()

        @keys.add("enter")
        def submit(event: KeyPressEvent) -> None:
            text = event.current_buffer.text
            if text.startswith("/"):
                try:
                    invocation = self.registry.parse(text, busy=self.busy)
                    if not self.ready and invocation.command.name not in {"help", "mode", "status", "quit"}:
                        raise ValueError("Still preparing. Your draft is preserved; /help and /mode are available.")
                except ValueError as exc:
                    self.emit(str(exc))
                    return
            elif self.wizard is None:
                if not self.ready or self.busy:
                    self.emit("Still preparing or working. Your draft is preserved; use /cancel to stop active work.")
                    return
                if not text.strip():
                    return
            event.current_buffer.validate_and_handle()

        @keys.add("escape", "enter")
        def newline(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text("\n")

        @keys.add("c-c")
        def interrupt(event: KeyPressEvent) -> None:
            if self.busy:
                event.app.create_background_task(self.cancel())
            elif self.wizard is not None:
                self.wizard = None
                self.emit("Setup cancelled; no publication was requested.")
            else:
                event.current_buffer.reset()

        @keys.add("c-d")
        def eof(event: KeyPressEvent) -> None:
            if event.current_buffer.text:
                event.current_buffer.delete()
            else:
                event.app.exit(result="/quit")

        @keys.add("c-o")
        def toggle(event: KeyPressEvent) -> None:
            self.status.mode_explicit = True
            self.status.mode = "detailed" if self.status.mode == "concise" else "concise"
            event.app.invalidate()

        return keys

    async def _lifetime(self) -> None:
        try:
            factory = await asyncio.to_thread(self.runtime_loader)
            if self.closing:
                return
            async with factory(self.request, self.directory, self.status, self.emit) as backend:
                self.backend = backend
                configured = await backend.initialize()
                self.ready = True
                self.status.state = "ready" if configured else "setup needed"
                if self.request.command == "setup":
                    self.wizard = SetupWizard()
                    self.emit(self.wizard.prompt())
                elif not configured:
                    self.emit("No model configured. Use /setup to choose Codex Sol, context budget, and permissions.")
                elif self.request.thread_id:
                    self.emit(f"Resumed {self.request.thread_id}. /history shows saved messages.")
                    self.emit(await backend.pending())
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
        # Library diagnostics use the same prompt-toolkit terminal lease rather
        # than writing over a live draft. Our own coalesced output bypasses the
        # proxy because flush() already holds that lease.
        with patch_stdout():
            await self._run()

    async def _run(self) -> None:
        self.emit(
            f"Agent UI · {self.directory}\n/help for commands · /mode concise|detailed · Ctrl+O toggles detail\nPreparing locally; you can type now. No model request is made until you send a prompt."
        )
        lifetime = asyncio.create_task(self._lifetime())
        flusher = asyncio.create_task(self._flusher())
        try:
            while not self.closing:
                try:
                    text = await self.session.prompt_async()
                except KeyboardInterrupt:
                    await self.cancel()
                    continue
                except EOFError:
                    break
                await self.handle(text)
        finally:
            self.closing = True
            await self.cancel()
            if self.job is not None:
                with suppress(asyncio.CancelledError):
                    await self.job
            self._stop.set()
            # Startup may still be importing. Let it finish off-loop, but never
            # enter a second terminal or migrate state in an orphan task.
            await lifetime
            flusher.cancel()
            with suppress(asyncio.CancelledError):
                await flusher
            self.renderer.finish()
            await self.flush()

    async def cancel(self) -> None:
        if self.wizard is not None and not self.busy:
            self.wizard = None
            self.emit("Setup cancelled. No new publication requested.")
            return
        if not self.busy:
            return
        self.status.state = "cancelling"
        if self.job_kind == "run" and self.backend is not None:
            await self.backend.cancel()
        elif self.job is not None:
            self.job.cancel()
        self.emit("Cancellation requested; waiting for cleanup.")

    def launch(self, operation: Coroutine[Any, Any, str], *, kind: str = "command") -> None:
        if self.busy:
            operation.close()
            self.emit("Still working. Use /cancel first.")
            return
        self.job_kind = kind
        self.status.state = "working" if kind == "run" else kind
        self.status.started = time.monotonic()
        if kind == "run":
            self.renderer.assistant_seen = False
            self.renderer.gap = False

        async def execute() -> None:
            try:
                result = await operation
                if result:
                    self.emit(result)
            except asyncio.CancelledError:
                self.emit("Cancelled. Deferred requests remain unapproved.")
            except Exception as exc:
                self.emit(f"Error: {exc}")
            finally:
                self.renderer.finish()
                if self.status.started is not None:
                    self.status.elapsed = time.monotonic() - self.status.started
                self.status.started = None
                self.status.state = "ready"
                await self.flush()

        self.job = asyncio.create_task(execute())

    async def handle(self, text: str) -> None:
        if text.startswith("/"):
            try:
                invocation = self.registry.parse(text, busy=self.busy)
                await self.command(invocation)
            except ValueError as exc:
                self.emit(str(exc))
            return
        if self.backend is None:
            self.emit("Still preparing. No prompt was sent.")
        elif self.wizard is not None:
            await self.setup_answer(text)
        elif text.strip():
            self.launch(self.backend.execute(self.renderer, prompt=text, flush=self.flush), kind="run")

    async def setup_answer(self, text: str) -> None:
        if self.backend is None or self.wizard is None:
            return
        try:
            if self.wizard.question is not None:
                self.wizard.accept(text)
                if self.wizard.question is None:
                    self.emit(await self.backend.preview_setup(self.wizard))
                self.emit(self.wizard.prompt())
            elif text.strip() == "yes":
                self.emit(await self.backend.publish_setup(self.wizard))
                self.wizard = None
            else:
                self.emit(self.wizard.prompt())
        except Exception as exc:
            self.emit(f"Setup failed: {exc}\n/cancel leaves setup. Existing files are preserved.")

    async def command(self, invocation: Invocation) -> None:
        name = invocation.command.name
        argument = invocation.arguments[0] if invocation.arguments else None
        if name == "help":
            self.emit(self.registry.help(argument))
        elif name == "quit":
            self.closing = True
        elif name == "mode":
            self.status.mode_explicit = True
            self.renderer.finish()
            self.status.mode = argument or ("detailed" if self.status.mode == "concise" else "concise")
            self.emit(
                f"Display: {self.status.mode}. Applies to subsequent stream events; use /history to review saved details."
            )
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
        elif name == "setup":
            self.wizard = SetupWizard()
            self.emit(self.wizard.prompt())
        elif name == "model":
            self.launch(self.backend.models(argument))
        elif name == "thinking":
            self.launch(self.backend.thinking(argument))
        elif name == "environment":
            self.launch(self.backend.set_environment(argument))
        elif name == "new":
            self.launch(self.backend.new())
        elif name == "resume":
            self.launch(self.backend.resume(argument))
        elif name == "review":
            assert argument is not None
            self.launch(self.backend.review(argument))
        elif name == "history":
            self.launch(self.backend.history(argument))
        elif name == "login":

            async def login() -> str:
                assert self.backend is not None
                account = await self.backend.app.login_model_account(argument or "codex")
                return f"Account: {account.provider.value} · {account.availability.value} · action: {account.required_action.value}"

            self.launch(login(), kind="login")
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
