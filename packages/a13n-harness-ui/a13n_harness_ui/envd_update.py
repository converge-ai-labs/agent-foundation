"""Explicit Envd update CLI; configuration selection without opening the App."""

from __future__ import annotations

from pathlib import Path

import click

from a13n_harness_ui.managed_runtime import ManagedEnvdRuntime, run_native_update, supports_native_update
from a13n_harness_ui.settings_loader import load_harness_ui_settings


async def update(
    *,
    config_path: Path | None,
    data_root: Path | None,
    executable: Path | None,
    version: str | None,
    check: bool,
    force: bool,
) -> None:
    source = await load_harness_ui_settings(config_path, data_root=data_root)
    if source.candidate_error is not None:
        raise source.candidate_error
    configured = None if source.configuration is None else source.configuration.document.envd
    selected = executable or (
        None if configured is None or configured.executable is None else Path(configured.executable)
    )
    if selected is not None:
        selected = selected.expanduser().resolve(strict=True)
        if not await supports_native_update(selected):
            raise click.ClickException(
                "This Envd does not support native updates. Install a release with `update` support "
                "using the standalone Envd installer, then retry."
            )
        if not check:
            click.echo("Stop daemons using this installation before updating. This command does not restart services.")
        click.echo(f"Updating Envd: {selected}" if not check else f"Checking Envd: {selected}")
        click.echo(await run_native_update(selected, version=version, check=check, force=force), nl=False)
        return

    root = source.settings.storage.data_root / "runtimes"
    runtime = ManagedEnvdRuntime(
        cache_root=root / "managed", staging_root=root / "staging", settings=source.settings.envd_runtime
    )
    status = await runtime.status()
    if status.error is not None:
        raise click.ClickException(status.error)
    if version is not None and version != status.required_version:
        raise click.UsageError(
            f"Managed Envd requires {status.required_version}; update Harness UI to select another version. "
            "Use --executable for a standalone installation."
        )
    click.echo(f"Managed Envd: {status.required_version}\nDestination: {status.executable}")
    if check:
        click.echo("Installed." if status.installed else "Not installed. Run without --check to acquire it.")
        return
    click.echo("Stop other Harness UI instances using this data root before replacing an installed runtime.")
    destination = await runtime.resolve(reinstall=force)
    click.echo(f"Envd ready: {destination}\nRestart Harness UI to use the selected runtime.")
