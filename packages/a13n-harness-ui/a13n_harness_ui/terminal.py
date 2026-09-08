"""Lightweight terminal entry. No App, provider, database, or GUI imports."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

import click

from a13n_harness_ui.updater import run_update

if TYPE_CHECKING:
    from a13n_harness_ui.cli import CliRequest


def start(request: CliRequest) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise click.ClickException(
            "Interactive mode requires a terminal. Use `a13n-harness-ui run <prompt>` for automation."
        )
    from a13n_harness_ui.interactive.startup import run_terminal

    try:
        update = asyncio.run(run_terminal(request))
        if update is not None:
            # Both terminal applications and the App have closed before the installer
            # inherits the normal terminal. No agent or background task can approve it.
            run_update(update)
    except click.exceptions.Exit:
        raise
    except KeyboardInterrupt:
        raise click.exceptions.Exit(130) from None
    except Exception as exc:
        from a13n_harness_ui.diagnostics import exception_feedback, terminal_traceback

        # asyncio.run has unwound the App and terminal contexts before stderr is used.
        click.echo(terminal_traceback(exc), err=True)
        click.echo(exception_feedback(exc, thread_id=request.thread_id, phase="terminal_startup"), err=True)
        raise click.exceptions.Exit(1) from exc
