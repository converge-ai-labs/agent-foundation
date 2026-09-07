"""One App lifetime spanning single-screen setup and the conversation terminal."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit import print_formatted_text

from .rendering import Status, terminal_text

if TYPE_CHECKING:
    from a13n_ui.cli import CliRequest

    from .backend import SessionBackend
    from .updates import UpdateCommand


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
    from .shell import CliShell
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
            factory = await asyncio.to_thread(runtime_loader)
            from a13n_ui.settings_loader import resolve_agent_ui_data_root

            async with factory(request, directory, status, emit) as backend:
                if not request.no_update_check:
                    configuration = await backend.app.current_configuration()
                    if configuration is None or configuration.document.process.terminal_update_check:
                        root = resolve_agent_ui_data_root(request.config_path, data_root=request.data_root)
                        update = await prompt_update(root, landing)
                        if update is not None:
                            return update
                configured = await backend.initialize()
                if request.command == "setup" or not configured:
                    landing.title = "Agent CLI · Setup"
                    completed = await run_setup(
                        backend.app, directory, ask_user=landing.ask, emit=emit, environment=backend.environment
                    )
                    if not completed or request.command == "setup":
                        await landing.close()
                        print_formatted_text(terminal_text(landing.notice))
                        return
                    if not await backend.initialize():
                        await landing.close()
                        print_formatted_text(
                            "The selected Agent still has no Model. Run a13n-ui setup or check --agent."
                        )
                        return
                await landing.close()
                shell = CliShell(request, directory=directory, status=status)
                await shell.run(backend)
    except asyncio.CancelledError:
        if not landing.cancel_requested:
            raise
        print_formatted_text("Startup cancelled. Completed credential or configuration writes are retained.")
        return
    # The App and terminal have both finished cleanup before these primary-screen hints.
    if status.session_id:
        print_formatted_text(terminal_text(resume_hint(request, status.session_id, directory)))
