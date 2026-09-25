"""Installed entry point; configure logging and metrics once and select explicit operations."""

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Literal, get_args

import click
import uvicorn
from a13n_logging import configure_logging
from pydantic import SecretStr, ValidationError

from a13n_service.app import build_app, check_schema, open_storage
from a13n_service.distribution import OSS
from a13n_service.infra.db import Storage
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.telemetry import serve_metrics
from a13n_service.migrations.runner import check, generate, heads, upgrade
from a13n_service.settings import ProcessRole, Settings, load_settings
from a13n_service.tenancy.bootstrap import AlreadyBootstrapped, BootstrapInput, bootstrap
from a13n_service.tenancy.users import set_account_status


@click.group()
@click.version_option(package_name="a13n-service")
@click.option("--config", type=click.Path(exists=True, path_type=Path))
@click.pass_context
def main(ctx: click.Context, config: Path | None) -> None:
    try:
        ctx.obj = load_settings(config)
    except (ValueError, OSError) as error:
        # Validation errors can contain input values, including deployment secrets.
        raise click.ClickException(f"Invalid Service configuration ({type(error).__name__})") from None
    telemetry = ctx.obj.telemetry
    # Only the server writes the log file; operator commands log to stdout, so they never share its file.
    serving = ctx.invoked_subcommand == "run"
    try:
        configure_logging(
            level=telemetry.log_level,
            log_format=telemetry.log_format,
            logger_names=("a13n_service", "a13n_harness"),
            stdout=telemetry.log_stdout or not serving,
            file=telemetry.log_output() if serving else None,
        )
    except ValueError as error:
        # An unwritable telemetry.log_file; the message names the path, never a secret.
        raise click.ClickException(f"Logging could not be configured: {error}") from None


@main.command()
@click.option("--role", type=click.Choice(get_args(ProcessRole)), default="all")
@click.pass_obj
def run(settings: Settings, role: ProcessRole) -> None:
    if settings.telemetry.metrics_port is not None:
        serve_metrics(settings.server.host, settings.telemetry.metrics_port)
    uvicorn.run(
        build_app(role=role, settings=settings),
        host=settings.server.host,
        port=settings.server.port,
        timeout_graceful_shutdown=settings.server.shutdown_timeout,
        log_config=None,
        access_log=False,
        ssl_certfile=str(settings.server.tls_certificate) if settings.server.tls_certificate else None,
        ssl_keyfile=str(settings.server.tls_key) if settings.server.tls_key else None,
        proxy_headers=bool(settings.server.trusted_proxies),
        forwarded_allow_ips=list(settings.server.trusted_proxies),
    )


@main.command()
@click.option("--generate", "message", help="Autogenerate a revision against a disposable database.")
@click.option("--check", "check_only", is_flag=True, help="Verify migration/metadata parity without changing schema.")
@click.pass_obj
def migrate(settings: Settings, message: str | None, check_only: bool) -> None:
    if message and check_only:
        raise click.UsageError("--generate and --check are mutually exclusive")
    if message:
        generate(settings.database, OSS, message)
    elif check_only:
        check(settings.database, OSS)
    else:
        upgrade(settings.database, OSS)


def _with_storage[T](settings: Settings, operation: Callable[[Storage], Awaitable[T]]) -> T:
    """Run one operation against a schema this build can use, then close the pool."""

    async def run() -> T:
        storage = open_storage(settings.database)
        try:
            await check_schema(storage, heads(OSS))
            return await operation(storage)
        finally:
            await storage.close()

    return asyncio.run(run())


@main.command("bootstrap")
@click.option("--email", required=True)
@click.option("--password-stdin", is_flag=True, help="Read the password from the first line of standard input.")
@click.pass_context
def bootstrap_command(ctx: click.Context, email: str, password_stdin: bool) -> None:
    """Create the first organization, workspace and administrator; exit 3 when already initialized."""
    if password_stdin:
        password = sys.stdin.readline().removesuffix("\n")
    else:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    try:
        request = BootstrapInput(email=email, password=SecretStr(password))
    except ValidationError:
        raise click.ClickException("Bootstrap refused: invalid email, or a password under 12 characters") from None
    try:
        result = _with_storage(ctx.obj, partial(bootstrap, request=request))
    except AlreadyBootstrapped:
        click.echo("Bootstrap refused: the Service is already initialized", err=True)
        ctx.exit(3)
    click.echo(json.dumps(asdict(result)))


@main.group()
def user() -> None:
    """Operator actions on user accounts, outside any tenant's authority."""


@user.command("disable")
@click.option("--email", required=True)
@click.pass_obj
def disable_user(settings: Settings, email: str) -> None:
    """Refuse the user's authentication; accepted work stops at its next authority refresh."""
    _set_status(settings, email, "disabled")


@user.command("enable")
@click.option("--email", required=True)
@click.pass_obj
def enable_user(settings: Settings, email: str) -> None:
    """Restore a disabled user with the grants and keys it had."""
    _set_status(settings, email, "active")


def _set_status(settings: Settings, email: str, status: Literal["active", "disabled"]) -> None:
    try:
        user_id = _with_storage(settings, partial(set_account_status, email=email.lower(), status=status))
    except ServiceError as error:
        raise click.ClickException(error.message) from None
    click.echo(json.dumps({"id": user_id, "status": status}))
