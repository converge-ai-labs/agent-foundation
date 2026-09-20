"""Command-line entry point for serving and database management."""

from __future__ import annotations

from pathlib import Path

import click

from a13n_service.configuration.sources import ConfigurationError, load_settings
from a13n_service.database import DatabaseMigrator
from a13n_service.log import configure_logging
from a13n_service.process.server import serve_app
from a13n_service.settings import ProcessRole, Settings


def _settings(*, overrides: dict | None = None) -> Settings:
    context = click.get_current_context().find_root()
    if overrides is None and "settings" in context.meta:
        return context.meta["settings"]
    try:
        settings = load_settings(context.obj, overrides=overrides)
        context.meta["settings"] = settings
        return settings
    except ConfigurationError as error:
        raise click.ClickException(str(error)) from None


def _migrator(settings: Settings | None = None) -> DatabaseMigrator:
    resolved = settings or _settings()
    return DatabaseMigrator(resolved.database_config(), resolved.migration_config())


@click.group()
@click.version_option(package_name="a13n-service")
@click.option(
    "--config",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Explicit Service TOML configuration file.",
)
@click.pass_context
def main(ctx: click.Context, config: Path | None) -> None:
    """Run and operate a13n Service."""
    ctx.obj = config


@main.command()
@click.option("--host", default=None, help="Bind host; defaults to A13N_SERVICE_HOST.")
@click.option("--role", default=None, type=click.Choice([role.value for role in ProcessRole]))
def serve(host: str | None, role: str | None) -> None:
    """Start the FastAPI service."""

    from a13n_service.app import create_app

    settings = _settings(
        overrides={"service": {key: value for key, value in {"role": role, "host": host}.items() if value is not None}}
    )
    configure_logging(settings)
    _prepare_database(settings)
    serve_app(create_app(settings), host=host)


@main.group()
def db() -> None:
    """Inspect and migrate the service database."""

    configure_logging(_settings())


@db.command()
@click.option("--revision", default="head", show_default=True)
def upgrade(revision: str) -> None:
    """Apply migrations through the requested revision."""

    _migrator().upgrade(revision)
    click.echo(f"Database upgraded to {revision}.")


@db.command()
@click.option("--revision", default="-1", show_default=True)
def downgrade(revision: str) -> None:
    """Downgrade one revision or to an explicitly reviewed target."""

    _migrator().downgrade(revision)
    click.echo(f"Database downgraded to {revision}.")


@db.command()
@click.option("--check-heads", is_flag=True, help="Fail unless every migration head is applied.")
def current(check_heads: bool) -> None:
    """Show the current database revision."""

    _migrator().current(check_heads=check_heads)


@db.command()
def history() -> None:
    """Show migration history."""

    _migrator().history()


@db.command()
@click.argument("message")
def migrate(message: str) -> None:
    """Autogenerate a revision; repository contributors should use make db-migrate."""

    _migrator().revision(message)
    click.echo(f"Migration generated: {message}")


@main.group()
def iam() -> None:
    """Operate local identity initialization from a protected terminal."""


@iam.command("reissue-bootstrap")
def reissue_bootstrap() -> None:
    """Invalidate the pending administrator link and issue its replacement."""
    import asyncio

    from a13n_service.iam.runtime import build_identity_runtime
    from a13n_service.storage.relational import create_session_factory, create_sql_engine

    async def reissue() -> None:
        settings = _settings()
        engine = create_sql_engine(settings.database_config())
        try:
            runtime = await build_identity_runtime(create_session_factory(engine), settings.identity_configuration())
            issued = await runtime.invitations.initialize(reissue=True)
            if issued is None:
                raise click.ClickException("Administrator initialization is already complete.")
            delivery = await runtime.invitations.deliver(issued)
            if delivery.invitation_url is not None:
                click.echo(delivery.invitation_url)
            elif delivery.delivery == "failed":
                raise click.ClickException("Invitation saved but email delivery failed. Fix SMTP and reissue.")
            else:
                click.echo("Administrator invitation sent.")
        finally:
            await engine.dispose()

    asyncio.run(reissue())


@main.group("config")
def config_commands() -> None:
    """Inspect the selected configuration without starting Service."""


@main.group()
def hooks() -> None:
    """Operate lifecycle Hook dispatch from a protected terminal."""


@hooks.command("retry-dispatch")
@click.argument("event_id")
@click.option("--organization-id", required=True, help="Organization that owns the failed lifecycle event.")
def retry_hook_dispatch(event_id: str, organization_id: str) -> None:
    """Requeue one failed event; completed dispatch is never repeated."""
    import asyncio

    from a13n_service.hooks.dispatch import retry_failed_hook_dispatch
    from a13n_service.storage import transaction
    from a13n_service.storage.relational import create_session_factory, create_sql_engine
    from a13n_service.temporal import utc_now

    async def retry() -> bool:
        settings = _settings()
        configure_logging(settings)
        engine = create_sql_engine(settings.database_config())
        try:
            async with transaction(create_session_factory(engine)) as database:
                return await retry_failed_hook_dispatch(
                    database, organization_id=organization_id, event_id=event_id, now=utc_now()
                )
        finally:
            await engine.dispose()

    if not asyncio.run(retry()):
        raise click.ClickException("No failed Hook dispatch found for that organization and event.")
    click.echo(f"Hook dispatch requeued: {event_id}")


@config_commands.command("check")
def check_config() -> None:
    _settings()
    click.echo("Service configuration is valid.")


def _prepare_database(settings: Settings) -> None:
    migrator = _migrator(settings)
    if settings.service.role in {ProcessRole.all, ProcessRole.control} and settings.migration.auto_migrate:
        migrator.upgrade()
    else:
        migrator.current(check_heads=True)


@config_commands.command("healthcheck")
def healthcheck() -> None:
    """Check the selected Service port; used by the container health probe."""
    import httpx2

    settings = _settings()
    host = settings.service.host
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1" if host == "0.0.0.0" else "[::1]"
    elif ":" in host:
        host = f"[{host}]"
    try:
        with httpx2.Client(trust_env=False, timeout=2) as client:
            client.get(f"http://{host}:{settings.service.port}/healthz").raise_for_status()
    except httpx2.HTTPError:
        raise click.ClickException("Service health probe failed") from None
