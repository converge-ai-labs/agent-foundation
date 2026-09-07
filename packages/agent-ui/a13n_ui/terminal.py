"""Lightweight terminal entry. No App, provider, database, or GUI imports."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from a13n_ui.cli import CliRequest


def start(request: CliRequest) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise click.ClickException("Interactive mode requires a terminal. Use `a13n-ui run <prompt>` for automation.")
    from a13n_ui.interactive.startup import run_terminal

    try:
        asyncio.run(run_terminal(request))
    except KeyboardInterrupt:
        raise click.exceptions.Exit(130) from None
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
