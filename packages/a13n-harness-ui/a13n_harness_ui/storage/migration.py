"""Programmatic Alembic runner for the Harness UI SQLite schema."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import URL, Connection, create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from .metadata import harness_ui_metadata

MIGRATIONS_PATH = Path(__file__).resolve().parent / "migrations"


class MigrationGraphError(RuntimeError):
    """The package migration history has no single unambiguous head."""


class DatabaseSchemaError(RuntimeError):
    """The database cannot support this package's required storage surface."""


class DatabaseMigrator:
    """Apply and inspect Harness UI's package-owned linear SQLite history."""

    def __init__(self, path: Path | str, *, busy_timeout_seconds: float = 5.0) -> None:
        resolved = Path(path).resolve(strict=False)
        self._path = resolved
        self._busy_timeout_seconds = busy_timeout_seconds

    def verify_history(self, *, allow_empty: bool = False) -> None:
        heads = self._heads()
        if len(heads) > 1 or (not allow_empty and len(heads) != 1):
            joined = ", ".join(sorted(heads)) or "none"
            raise MigrationGraphError(f"Harness UI migration history must have one head; found: {joined}")

    def upgrade(self) -> None:
        self.verify_history()

        def upgrade_known_history(config: Config) -> None:
            connection = self._connection(config)
            current = self._current_head(connection)
            known = {revision.revision for revision in ScriptDirectory.from_config(config).walk_revisions()}
            if current is not None and current not in known:
                # A newer compatible package owns this head. Never downgrade,
                # stamp, or pass an unknown future revision to older Alembic code.
                self._verify_structure(connection)
                return
            command.upgrade(config, "head")

        self._run(upgrade_known_history, write=True)

    def verify_current(self) -> None:
        self.verify_history()

        def verify(config: Config) -> None:
            connection = self._connection(config)
            if self._current_head(connection) is None:
                raise DatabaseSchemaError("Harness UI database has no migration revision")
            self._verify_structure(connection)

        self._run(verify)

    @staticmethod
    def _connection(config: Config) -> Connection:
        connection = config.attributes.get("connection")
        if not isinstance(connection, Connection):
            raise RuntimeError("Harness UI migration verification requires a SQLAlchemy connection")
        return connection

    @staticmethod
    def _current_head(connection: Connection) -> str | None:
        heads = MigrationContext.configure(connection).get_current_heads()
        if len(heads) > 1:
            raise DatabaseSchemaError("Harness UI database must have a single migration head")
        return heads[0] if heads else None

    @staticmethod
    def _verify_structure(connection: Connection) -> None:
        # Extra tables/columns are compatible expansion, not grounds to stop an
        # App. Migration authors must preserve old read/write semantics;
        # structural inspection cannot prove payload or behavioral compatibility.
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        for table in harness_ui_metadata().sorted_tables:
            if table.name not in tables:
                raise DatabaseSchemaError(f"Harness UI database is missing required table {table.name}")
            columns = {column["name"] for column in inspector.get_columns(table.name)}
            missing = set(table.columns.keys()) - columns
            if missing:
                raise DatabaseSchemaError(
                    f"Harness UI database table {table.name} is missing required columns: {', '.join(sorted(missing))}"
                )

    def revision(self, message: str) -> None:
        if not message.strip():
            raise ValueError("migration message must not be empty")
        self.verify_history(allow_empty=True)
        if self._heads():
            self._run(lambda config: command.upgrade(config, "head"), write=True)
        self._run(lambda config: command.revision(config, message=message, autogenerate=True))

    def _run(self, operation: Callable[[Config], object], *, write: bool = False) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        engine = self._engine()
        try:
            with engine.connect() as connection:
                self._configure(connection, foreign_keys=not write)
                if write:
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                config = self._config()
                config.attributes["connection"] = connection
                operation(config)
                if connection.in_transaction():
                    connection.commit()
                if write:
                    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                    if violations:
                        raise DatabaseSchemaError("Harness UI migration left invalid foreign keys")
        finally:
            engine.dispose()

    def _engine(self) -> Engine:
        return create_engine(
            URL.create("sqlite", database=str(self._path)),
            poolclass=NullPool,
            connect_args={"timeout": self._busy_timeout_seconds},
        )

    def _heads(self) -> list[str]:
        return ScriptDirectory.from_config(self._config()).get_heads()

    def _config(self) -> Config:
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_PATH))
        config.set_main_option("file_template", "%%(year)d%%(month).2d%%(day).2d_%%(rev)s_%%(slug)s")
        config.set_main_option("timezone", "UTC")
        return config

    def _configure(self, connection: Connection, *, foreign_keys: bool = True) -> None:
        connection.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
        connection.exec_driver_sql(f"PRAGMA busy_timeout={int(self._busy_timeout_seconds * 1000)}")
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("PRAGMA synchronous=FULL")
        if connection.in_transaction():
            connection.commit()


__all__ = ["DatabaseMigrator", "DatabaseSchemaError", "MigrationGraphError"]
