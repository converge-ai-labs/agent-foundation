"""Lazy Textual launch boundary."""

from __future__ import annotations

from pathlib import Path

from a13n_ui.surfaces import NewThreadDefaults
from a13n_ui.tui.controller import AppContextFactory


async def launch_terminal(
    *,
    app_factory: AppContextFactory,
    launch_directory: Path,
    launch_thread_id: str | None = None,
    launch_defaults: NewThreadDefaults | None = None,
    show_setup: bool = False,
) -> None:
    from a13n_ui.tui.application import AgentUiTerminalApp

    app = AgentUiTerminalApp(
        app_factory=app_factory,
        launch_directory=launch_directory,
        launch_thread_id=launch_thread_id,
        launch_defaults=launch_defaults,
        show_setup=show_setup,
    )
    try:
        await app.run_async()
    finally:
        # Textual restores the terminal before App-owned cleanup can wait.
        await app.controller.close()


__all__ = ["launch_terminal"]
