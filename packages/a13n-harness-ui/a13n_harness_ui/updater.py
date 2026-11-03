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


def _windows_update_pids() -> list[int]:
    """Include uv's executable launcher and any virtualenv Python redirectors."""
    import psutil

    prefix = Path(sys.prefix).resolve()
    entrypoint = Path(sys.argv[0])
    # uv's console-script stub strips .exe from argv[0].
    if not entrypoint.suffix:
        entrypoint = entrypoint.with_suffix(".exe")
    entrypoint = entrypoint.resolve()
    pids = [os.getpid()]
    try:
        for parent in psutil.Process().parents():
            executable = Path(parent.exe()).resolve()
            if executable != entrypoint and not executable.is_relative_to(prefix):
                break
            pids.append(parent.pid)
    except psutil.Error as exc:
        raise OSError(f"Could not identify the launching processes: {exc}") from exc
    return pids


def _start_windows_update(command: UpdateCommand) -> None:
    from a13n_harness_ui import _windows_update

    # Running from the tool's Python or importing its packages in the worker
    # would keep the installation locked. Snapshot the stdlib-only worker and
    # use the base interpreter without site initialization or PYTHONPATH.
    source = Path(_windows_update.__file__).read_text(encoding="utf-8")
    with _windows_update.process_handles(_windows_update_pids()) as handles:
        startup = subprocess.STARTUPINFO(lpAttributeList={"handle_list": handles})
        subprocess.Popen(
            (
                str(Path(sys.base_prefix) / "python.exe"),
                "-I",
                "-S",
                "-c",
                source,
                command.executable,
                str(command.tool_directory),
                *(str(handle) for handle in handles),
            ),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            startupinfo=startup,
            close_fds=True,
        )


def run_update(command: UpdateCommand) -> None:
    """Run once, or hand off on Windows so this process can release its files."""
    if sys.platform == "win32":
        try:
            _start_windows_update(command)
        except KeyboardInterrupt:
            click.echo("Update handoff interrupted. Check the update window before retrying.", err=True)
            raise click.exceptions.Exit(130) from None
        except OSError as exc:
            raise click.ClickException(
                f"Could not start the update window: {exc}. No automatic retry was attempted."
            ) from exc
        click.echo("Update handed off to a separate window; installation is not complete yet.")
        click.echo(
            "This command will exit so the updater can replace its files. Check the update window for the result."
        )
        return
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
