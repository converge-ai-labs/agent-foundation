"""One App lifetime spanning single-screen setup and the conversation terminal."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit import print_formatted_text

from .rendering import Status, terminal_text

if TYPE_CHECKING:
    from a13n_harness_ui.cli import CliRequest
    from a13n_harness_ui.updater import UpdateCommand

    from .backend import SessionBackend
    from .shell import CliShell


def _load_runtime() -> Callable[..., AbstractAsyncContextManager[SessionBackend]]:
    from .runtime import open_session

    return open_session


async def run_terminal(
    request: CliRequest,
    *,
    directory: Path | None = None,
    runtime_loader: Callable[[], Callable[..., AbstractAsyncContextManager[SessionBackend]]] = _load_runtime,
) -> UpdateCommand | None:
    from .lifecycle import resume_hint
    from .onboarding import LandingScreen, run_setup
    from .updates import prompt_update

    directory = (directory or Path.cwd()).resolve()
    status = Status(mode=request.display or "concise", mode_explicit=request.display is not None)
    shell: CliShell | None = None

    landing = LandingScreen()

    def emit(text: str) -> None:
        if shell is not None:
            shell.emit(text)
        else:
            landing.emit(text)

    try:
        async with landing:
            started = time.perf_counter()
            landing.emit("Loading runtime…")
            factory = await asyncio.to_thread(runtime_loader)
            runtime_loaded = time.perf_counter()
            landing.emit("Opening local storage and configuration…")
            from a13n_harness_ui.settings_loader import resolve_harness_ui_data_root

            async with factory(request, directory, status, emit) as backend:
                logger = logging.getLogger("a13n_harness_ui.startup")
                logger.info(
                    "Startup runtime_import=%.3fs app_open=%.3fs",
                    runtime_loaded - started,
                    time.perf_counter() - runtime_loaded,
                )
                if not request.no_update_check:
                    configuration = await backend.app.current_configuration()
                    if configuration is None or configuration.document.process.terminal_update_check:
                        root = resolve_harness_ui_data_root(request.config_path, data_root=request.data_root)
                        update = await prompt_update(root, landing)
                        if update is not None:
                            return update
                landing.emit("Restoring session…" if request.thread_id else "Preparing session…")
                session_started = time.perf_counter()
                configured = await backend.initialize()
                logger.info("Startup session_initialize=%.3fs", time.perf_counter() - session_started)
                if request.command in {"setup", "add"} or not configured:
                    landing.title = (
                        f"Harness UI · Add {request.action}" if request.command == "add" else "Harness UI · Setup"
                    )
                    completed = await run_setup(
                        backend.app,
                        directory,
                        ask_user=landing.ask,
                        emit=emit,
                        environment=backend.environment,
                        add_model=request.command == "add" and request.action == "model",
                        add_agent=request.command == "add" and request.action != "model",
                        advanced=request.setup_advanced,
                    )
                    if not completed or request.command in {"setup", "add"}:
                        await landing.close()
                        print_formatted_text(terminal_text(landing.notice))
                        return
                    if not await backend.initialize():
                        await landing.close()
                        print_formatted_text(
                            "The selected Agent still has no Model. Run a13n-harness-ui setup or check --agent."
                        )
                        return
                from .shell import CliShell

                shell = CliShell(request, directory=directory, status=status)
                await landing.run_chat(shell, backend)
    except asyncio.CancelledError:
        if not landing.cancel_requested:
            raise
        print_formatted_text("Startup cancelled. Completed credential or configuration writes are retained.")
        return
    # The App and terminal have both finished cleanup before these primary-screen hints.
    if status.session_id:
        print_formatted_text(terminal_text(resume_hint(request, status.session_id, directory)))
