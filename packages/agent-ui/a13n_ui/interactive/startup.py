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


def _load_runtime() -> Callable[..., AbstractAsyncContextManager[SessionBackend]]:
    from .runtime import open_session

    return open_session


async def run_terminal(
    request: CliRequest,
    *,
    directory: Path | None = None,
    runtime_loader: Callable[[], Callable[..., AbstractAsyncContextManager[SessionBackend]]] = _load_runtime,
) -> None:
    from .lifecycle import resume_hint
    from .onboarding import LandingScreen, run_setup
    from .shell import CliShell

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
            async with factory(request, directory, status, emit) as backend:
                configured = await backend.initialize()
                if request.command == "setup" or not configured:
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
        print_formatted_text("Setup cancelled. Completed credential or configuration writes are retained.")
        return
    # The App and terminal have both finished cleanup before these primary-screen hints.
    if status.update_notice:
        print_formatted_text(terminal_text(status.update_notice))
    if status.session_id:
        print_formatted_text(terminal_text(resume_hint(request, status.session_id, directory)))
