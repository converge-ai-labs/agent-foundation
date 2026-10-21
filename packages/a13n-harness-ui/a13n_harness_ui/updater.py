"""Explicit package-manager handoff shared by the CLI and startup prompt."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import click


@dataclass(frozen=True, slots=True)
class UpdateCommand:
    """A recognized uv-tool installation, not a user-supplied shell command."""

    executable: str
    tool_directory: Path

    @property
    def argv(self) -> tuple[str, ...]:
        return (self.executable, "tool", "upgrade", "a13n-harness-ui")


def update_command() -> UpdateCommand | None:
    """Only the documented uv-tool installation has an automatic command recipe."""
    prefix = Path(sys.prefix)
    executable = shutil.which("uv")
    if executable is None or prefix.name != "a13n-harness-ui" or not (prefix / "uv-receipt.toml").is_file():
        return None
    return UpdateCommand(executable, prefix.parent)


def run_update(command: UpdateCommand) -> None:
    """Run an explicitly requested update in the ordinary terminal, never retrying."""
    click.echo("Running: uv tool upgrade a13n-harness-ui")
    click.echo(f"Tool directory: {command.tool_directory}")
    try:
        result = subprocess.run(
            command.argv,
            env={**os.environ, "UV_TOOL_DIR": str(command.tool_directory)},
            check=False,
        )
    except KeyboardInterrupt:
        click.echo("Update interrupted. Check the installation before retrying.", err=True)
        raise click.exceptions.Exit(130) from None
    except OSError as exc:
        raise click.ClickException(f"Could not run uv: {exc}. No automatic retry was attempted.") from exc
    if result.returncode:
        click.echo("Update failed. No automatic retry was attempted.", err=True)
        raise click.exceptions.Exit(result.returncode if result.returncode > 0 else 1)
    click.echo("Update complete. Restart a13n-harness-ui to use the new version.")


def update() -> None:
    """The explicit command itself authorizes updating this uv-tool installation."""
    command = update_command()
    if command is None:
        raise click.ClickException(
            "No recognized uv-tool installation or uv executable found. "
            "Update with the original package manager. "
            "For uv-tool installations, make uv available on PATH and run: uv tool upgrade a13n-harness-ui"
        )
    run_update(command)
