"""Interactive terminal adapter with strict TTY and lazy Textual imports."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from a13n_ui.errors import ConfigurationError
from a13n_ui.surfaces import NewThreadDefaults

if TYPE_CHECKING:
    from a13n_ui.tui.controller import AppContextFactory


@dataclass(frozen=True, slots=True)
class TuiLaunchOptions:
    """Terminal-local initial selection that never mutates file defaults."""

    thread_id: str | None = None
    defaults: NewThreadDefaults = field(default_factory=NewThreadDefaults)
    open_workbench: bool = False


async def run(
    app_factory: AppContextFactory,
    *,
    launch: TuiLaunchOptions | None = None,
    launch_directory: Path | None = None,
    stdin_isatty: Callable[[], bool] | None = None,
    stdout_isatty: Callable[[], bool] | None = None,
) -> None:
    """Validate the terminal and lazily run Textual against one App factory."""

    input_check = stdin_isatty or sys.stdin.isatty
    output_check = stdout_isatty or sys.stdout.isatty
    if not input_check() or not output_check():
        raise ConfigurationError(
            "The full-screen TUI requires an interactive input and output terminal. "
            "Use `a13n-ui run <prompt>` for non-interactive execution.",
            code="tui_tty_required",
        )
    from a13n_ui.tui.launch import launch_terminal

    selected = launch or TuiLaunchOptions()
    await launch_terminal(
        app_factory=app_factory,
        launch_directory=(launch_directory or Path(os.getcwd())).resolve(strict=True),
        launch_thread_id=selected.thread_id,
        launch_defaults=selected.defaults,
        open_workbench=selected.open_workbench,
    )


__all__ = ["TuiLaunchOptions", "run"]
