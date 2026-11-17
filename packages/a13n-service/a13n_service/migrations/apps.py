"""Independent App histories over the Service database, with retained namespace ownership."""

import asyncio
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, delete, insert, select, text

from a13n_service.app_database import AppDatabase, DatabaseRow
from a13n_service.distribution import Distribution
from a13n_service.infra.db import Storage, short_session
from a13n_service.migrations import runner
from a13n_service.migrations.app_render import make_portable
from a13n_service.migrations.scratch import scratch_database
from a13n_service.settings import Database

_ENV = Path(__file__).with_name("app_env")


@contextmanager
def configuration(database: AppDatabase) -> Iterator[Config]:
    database.validate()
    package, _, directory = database.migrations.partition(":")
    resource = files(package).joinpath(directory)
    with as_file(resource) as root:
        if not (root / "versions").is_dir():
            raise ValueError(f"App {database.app_id} migrations are missing; run a13n-service apps db init")
        config = Config()
        config.set_main_option("script_location", str(_ENV))
        config.set_main_option("version_locations", str(root / "versions"))
        config.set_main_option("path_separator", "os")
        config.set_main_option("file_template", "%%(year)d%%(month).2d%%(day).2d_%%(rev)s_%%(slug)s")
        config.attributes["app_database"] = database
        script = ScriptDirectory.from_config(config)
        if len(script.get_heads()) > 1:
            raise ValueError(f"App {database.app_id} migration graph must have at most one head")
        yield config


def initialize(database: AppDatabase) -> Path:
    """Initialize only a package's migration directory, without a connection or generated models."""
    package, _, directory = database.migrations.partition(":")
    resource = files(package)
    if not isinstance(resource, Path):
        raise ValueError("Initialize migrations in an editable source package")
    root = resource / directory
    versions = root / "versions"
    if versions.exists():
        raise ValueError(f"Migration directory already exists: {versions}")
    versions.mkdir(parents=True)
    return root


def heads(database: AppDatabase) -> tuple[str, ...]:
    with configuration(database) as config:
        return tuple(ScriptDirectory.from_config(config).get_heads())


def _require_core(connection: Connection, distribution: Distribution) -> None:
    actual = MigrationContext.configure(connection).get_current_heads()
    expected = runner.heads(distribution)
    if not expected or tuple(sorted(actual)) != tuple(sorted(expected)):
        raise ValueError("Core database schema is incompatible; run a13n-service migrate first")


def _owner(connection: Connection, database: AppDatabase, *, claim: bool) -> bool:
    row = (
        connection.execute(
            select(DatabaseRow.__table__).where(
                (DatabaseRow.app_id == database.app_id) | (DatabaseRow.prefix == database.prefix)
            )
        )
        .mappings()
        .first()
    )
    if row is not None:
        if row["app_id"] != database.app_id or row["prefix"] != database.prefix:
            raise ValueError(
                f"App {database.app_id} configured prefix {database.prefix} conflicts with retained ownership "
                f"({row['app_id']}: {row['prefix']}); restore the original configuration; changing a prefix does not move data"
            )
        return True
    if not claim:
        return False
    if _occupied(connection, database):
        raise ValueError(f"App {database.app_id} namespace is occupied without a retained owner")
    connection.execute(insert(DatabaseRow).values(app_id=database.app_id, prefix=database.prefix))
    return False


def _occupied(connection: Connection, database: AppDatabase) -> bool:
    # Include relations, standalone types and functions, including partially committed migration DDL.
    return bool(
        connection.scalar(
            text("""
            SELECT EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND starts_with(c.relname, :prefix)
                UNION ALL
                SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                WHERE n.nspname='public' AND starts_with(t.typname, :prefix)
                UNION ALL
                SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND starts_with(p.proname, :prefix)
            )
        """),
            {"prefix": database.prefix},
        )
    )


@contextmanager
def migration_connection(settings: Database, distribution: Distribution, database: AppDatabase) -> Iterator[Config]:
    with configuration(database) as config, runner.locked_connection(settings) as connection:
        _require_core(connection, distribution)
        connection.commit()
        config.attributes["connection"] = connection
        yield config


def upgrade(settings: Database, distribution: Distribution, database: AppDatabase) -> None:
    with migration_connection(settings, distribution, database) as config:
        if not ScriptDirectory.from_config(config).get_heads():
            raise ValueError(f"App {database.app_id} has no migration revision; generate one before upgrade")
        connection = config.attributes["connection"]
        existing = _owner(connection, database, claim=True)
        # Retain ownership across reviewed autocommit blocks, including partially committed failures.
        connection.commit()
        try:
            command.upgrade(config, "head")
        except Exception:
            connection.rollback()
            if not existing and not _occupied(connection, database):
                connection.execute(delete(DatabaseRow).where(DatabaseRow.app_id == database.app_id))
                connection.commit()
            raise


def status(settings: Database, distribution: Distribution, database: AppDatabase) -> dict[str, Any]:
    with migration_connection(settings, distribution, database) as config:
        connection = config.attributes["connection"]
        bound = _owner(connection, database, claim=False)
        current = MigrationContext.configure(
            connection, opts={"version_table": database.version_table}
        ).get_current_heads()
        expected = tuple(ScriptDirectory.from_config(config).get_heads())
        return {
            "app_id": database.app_id,
            "prefix": database.prefix,
            "current": current,
            "head": expected,
            "bound": bound,
            "ready": bound and bool(expected) and current == expected,
        }


def generate(settings: Database, distribution: Distribution, database: AppDatabase, message: str) -> Path:
    """Replay Core and this App in an isolated database, then write a portable source revision."""
    package, _, directory = database.migrations.partition(":")
    if not isinstance(files(package).joinpath(directory), Path):
        raise ValueError("Generate migrations in an editable source package")
    with scratch_database(settings) as scratch:
        runner.upgrade(scratch, distribution)
        with migration_connection(scratch, distribution, database) as config:
            command.upgrade(config, "head")
            # Revision generation is source-only. The scratch namespace needs no persistent owner.
            script = command.revision(config, message=message, autogenerate=True)
            assert script is not None and not isinstance(script, list)
            path = Path(script.path)
            make_portable(path, database)
            return path


def check(settings: Database, distribution: Distribution, database: AppDatabase) -> None:
    """Package/metadata parity, independent of the state of any existing business database."""
    with scratch_database(settings) as scratch:
        runner.upgrade(scratch, distribution)
        upgrade(scratch, distribution, database)
        with migration_connection(scratch, distribution, database) as config:
            command.check(config)


def check_live(settings: Database, distribution: Distribution, database: AppDatabase) -> None:
    """Read-only parity against the deployment database; no scratch-database privilege required."""
    with migration_connection(settings, distribution, database) as config:
        connection = config.attributes["connection"]
        bound = _owner(connection, database, claim=False)
        expected = tuple(ScriptDirectory.from_config(config).get_heads())
        actual = MigrationContext.configure(
            connection, opts={"version_table": database.version_table}
        ).get_current_heads()
        if not bound or not expected or actual != expected:
            raise ValueError(f"App {database.app_id} database is not current; run a13n-service migrate")
        command.check(config)


async def require_current(
    storage: Storage,
    databases: Sequence[AppDatabase],
    *,
    expected: Mapping[str, tuple[str, ...]] | None = None,
) -> None:
    """Read-only startup/readiness gate; workers and organization enablement never run DDL."""
    if not databases:
        return
    # Extracted wheel resources stay outside SQL transactions and off the event loop.
    if expected is None:
        expected = {database.app_id: await asyncio.to_thread(heads, database) for database in databases}
    async with short_session(storage) as session:
        for database in databases:
            required = expected[database.app_id]
            prefix = await session.scalar(select(DatabaseRow.prefix).where(DatabaseRow.app_id == database.app_id))
            if prefix is not None and prefix != database.prefix:
                raise RuntimeError(
                    f"App {database.app_id} configured prefix {database.prefix} differs from retained prefix {prefix}; "
                    "restore the original configuration; changing a prefix does not move data"
                )
            if prefix is None or not required:
                raise RuntimeError(
                    f"App {database.app_id} schema is incompatible; run a13n-service apps db upgrade --app {database.app_id}"
                )
            exists = await session.scalar(
                text("SELECT to_regclass(:table)"), {"table": f"public.{database.version_table}"}
            )
            actual = (
                ()
                if exists is None
                else tuple(
                    (
                        await session.execute(
                            text(f'SELECT version_num FROM "{database.version_table}" ORDER BY version_num')
                        )
                    ).scalars()
                )
            )
            if actual != tuple(sorted(required)):
                raise RuntimeError(
                    f"App {database.app_id} schema is incompatible; run a13n-service apps db upgrade --app {database.app_id}"
                )
