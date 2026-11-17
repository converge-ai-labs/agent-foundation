"""Developer and operator commands for App-owned migrations; discovery belongs to the root CLI."""

import json
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import click
from alembic.util.exc import CommandError
from sqlalchemy.exc import SQLAlchemyError

from a13n_service.app_database import AppDatabase
from a13n_service.app_definitions import app_databases
from a13n_service.distribution import OSS
from a13n_service.migrations import apps as migrations
from a13n_service.settings import Database


@contextmanager
def migration_errors() -> Iterator[None]:
    try:
        yield
    except (ValueError, RuntimeError, CommandError) as error:
        raise click.ClickException(str(error)) from None
    except (SQLAlchemyError, OSError) as error:
        # Driver errors can include SQL values and credential-bearing connection strings.
        raise click.ClickException(f"App database operation failed ({type(error).__name__})") from None


def _database(ctx: click.Context, app_id: str | None) -> AppDatabase:
    databases = app_databases(ctx.meta["app_plugins"])
    if app_id is None:
        project = Path.cwd() / "pyproject.toml"
        if project.is_file():
            entries = (
                tomllib.loads(project.read_text())
                .get("project", {})
                .get("entry-points", {})
                .get("a13n_service.app_plugins", {})
            )
            if entries:
                databases = tuple(database for database in databases if database.app_id in entries)
    else:
        databases = tuple(database for database in databases if database.app_id == app_id)
    if len(databases) != 1:
        raise click.UsageError("Select one installed App declaring a database with --app APP_ID")
    return databases[0]


def _scratch_settings(ctx: click.Context, scratch_url: str | None) -> Database:
    from pydantic import SecretStr

    settings = ctx.obj.database
    return settings if scratch_url is None else settings.model_copy(update={"url": SecretStr(scratch_url)})


@click.group("apps")
def apps() -> None:
    """Develop and operate installed App plugins."""


@apps.group()
def db() -> None:
    """Independent App migrations; organization enablement never prepares a schema."""


@db.command("init")
@click.option("--app", "app_id")
@click.pass_context
def initialize(ctx: click.Context, app_id: str | None) -> None:
    """Create the declared package migration directory in an editable source checkout."""
    with migration_errors():
        click.echo(migrations.initialize(_database(ctx, app_id)))


@db.command("revision")
@click.option("--app", "app_id")
@click.option("-m", "--message", required=True)
@click.option("--scratch-url", help="PostgreSQL development/CI server with CREATE DATABASE permission.")
@click.pass_context
def revision(ctx: click.Context, app_id: str | None, message: str, scratch_url: str | None) -> None:
    """Replay history in a new disposable database and autogenerate a portable revision."""
    with migration_errors():
        click.echo(migrations.generate(_scratch_settings(ctx, scratch_url), OSS, _database(ctx, app_id), message))


@db.command("status")
@click.option("--app", "app_id")
@click.pass_context
def status(ctx: click.Context, app_id: str | None) -> None:
    """Report retained ownership, installed head and applied revisions without changing schema."""
    with migration_errors():
        click.echo(json.dumps(migrations.status(ctx.obj.database, OSS, _database(ctx, app_id))))


@db.command("check")
@click.option("--app", "app_id")
@click.option("--scratch-url", help="PostgreSQL development/CI server with CREATE DATABASE permission.")
@click.pass_context
def check(ctx: click.Context, app_id: str | None, scratch_url: str | None) -> None:
    """Prove packaged history matches models using a new disposable database."""
    with migration_errors():
        migrations.check(_scratch_settings(ctx, scratch_url), OSS, _database(ctx, app_id))
        click.echo("App migration history matches metadata")


@db.command("upgrade")
@click.option("--app", "app_id")
@click.pass_context
def upgrade(ctx: click.Context, app_id: str | None) -> None:
    """Explicitly apply one App's packaged history after the Core schema is current."""
    with migration_errors():
        database = _database(ctx, app_id)
        migrations.upgrade(ctx.obj.database, OSS, database)
        click.echo(json.dumps(migrations.status(ctx.obj.database, OSS, database)))
