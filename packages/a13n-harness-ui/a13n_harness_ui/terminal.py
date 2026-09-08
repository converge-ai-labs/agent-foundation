"""Lightweight terminal entry. No App, provider, database, or GUI imports."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import click

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
            click.echo("Running: uv tool upgrade a13n-harness-ui")
            result = subprocess.run(
                update.argv,
                env={**os.environ, "UV_TOOL_DIR": str(update.tool_directory)},
                check=False,
            )
            if result.returncode:
                click.echo("Update failed. No automatic retry was attempted.", err=True)
                raise click.exceptions.Exit(result.returncode if result.returncode > 0 else 1)
            click.echo("Update complete. Restart a13n-harness-ui to use the new version.")
    except click.exceptions.Exit:
        raise
    except KeyboardInterrupt:
        raise click.exceptions.Exit(130) from None
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
