"""Command-line entry point for serving and database management."""

from __future__ import annotations

import click
import uvicorn

from a13n_service.database import DatabaseMigrator
from a13n_service.log import configure_logging
from a13n_service.settings import ProcessRole, Settings, get_settings


def _migrator(settings: Settings | None = None) -> DatabaseMigrator:
    resolved = settings or get_settings()
    return DatabaseMigrator(resolved.database_config(), resolved.migration_config())


@click.group()
def main() -> None:
    """Run and operate Foundation Service."""


@main.command()
@click.option("--host", default=None, help="Bind host; defaults to FOUNDATION_HOST.")
@click.option("--role", default=None, type=click.Choice([role.value for role in ProcessRole]))
def serve(host: str | None, role: str | None) -> None:
    """Start the FastAPI service."""

    from a13n_service.app import create_app

    settings = get_settings()
    if role is not None:
        settings = settings.model_copy(update={"role": ProcessRole(role)})
    configure_logging(settings)
    uvicorn.run(
        create_app(settings),
        host=host or settings.host,
        port=settings.port,
        log_config=None,
        workers=1,
    )


@main.group()
def db() -> None:
    """Inspect and migrate the service database."""

    configure_logging(get_settings())


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
