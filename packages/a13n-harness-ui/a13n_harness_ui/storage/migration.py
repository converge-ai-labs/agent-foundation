"""Programmatic Alembic runner for the Harness UI SQLite schema."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import URL, Connection, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

MIGRATIONS_PATH = Path(__file__).resolve().parent / "migrations"


class MigrationGraphError(RuntimeError):
    """The package migration history has no single unambiguous head."""


class DatabaseSchemaError(RuntimeError):
    """The database revision does not match the package migration head."""


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
        self._run(lambda config: command.upgrade(config, "head"), write=True)

    def verify_current(self) -> None:
        self.verify_history()
        expected_heads = set(self._heads())

        def verify(config: Config) -> None:
            connection = config.attributes.get("connection")
            if not isinstance(connection, Connection):
                raise RuntimeError("Harness UI migration verification requires a SQLAlchemy connection")
            current_heads = set(MigrationContext.configure(connection).get_current_heads())
            if current_heads != expected_heads:
                current = ", ".join(sorted(current_heads)) or "none"
                expected = ", ".join(sorted(expected_heads))
                raise DatabaseSchemaError(
                    f"Harness UI database revision must match package head {expected}; found: {current}"
                )

        self._run(verify)

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
