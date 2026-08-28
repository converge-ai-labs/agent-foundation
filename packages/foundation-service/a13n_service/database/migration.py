"""Programmatic Alembic runner for the Foundation Service schema."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from a13n_service.storage.config import DatabaseConfig, PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url

from .config import MigrationConfig

MIGRATIONS_PATH = Path(__file__).resolve().parent / "migrations"
_MIGRATION_LOCK_NAME = "a13n-service:relational-schema"


class MigrationGraphError(RuntimeError):
    """The accepted migration graph has no single unambiguous head."""


class DatabaseMigrator:
    """Apply and inspect one service-wide Alembic history."""

    def __init__(
        self,
        database: DatabaseConfig,
        migration: MigrationConfig | None = None,
        *,
        script_location: Path = MIGRATIONS_PATH,
    ) -> None:
        if isinstance(database, SQLiteConfig) and str(database.path) == ":memory:":
            raise ValueError("schema migrations require a file-backed SQLite database")
        self._database = database
        self._migration = migration or MigrationConfig()
        self._script_location = script_location.resolve()

    def verify_history(self) -> None:
        heads = ScriptDirectory.from_config(self._alembic_config()).get_heads()
        if len(heads) > 1:
            joined = ", ".join(sorted(heads))
            raise MigrationGraphError(f"migration history must have one head; found: {joined}")

    def upgrade(self, revision: str = "head") -> None:
        self._run_online(lambda config: command.upgrade(config, revision), lock=True)

    def downgrade(self, revision: str = "-1") -> None:
        self._run_online(lambda config: command.downgrade(config, revision), lock=True)

    def current(self, *, check_heads: bool = False, verbose: bool = True) -> None:
        self._run_online(
            lambda config: command.current(config, verbose=verbose, check_heads=check_heads),
            lock=False,
        )

    def history(self, *, verbose: bool = True) -> None:
        self.verify_history()
        command.history(self._alembic_config(), verbose=verbose)

    def revision(self, message: str) -> None:
        if not message.strip():
            raise ValueError("migration message must not be empty")
        self._run_online(
            lambda config: command.revision(config, message=message, autogenerate=True),
            lock=True,
        )

    def _run_online(self, operation: Callable[[Config], object], *, lock: bool) -> None:
        self.verify_history()
        engine = self._create_engine()
        try:
            with engine.connect() as connection:
                self._configure_sqlite(connection)
                with self._migration_lock(connection, enabled=lock):
                    config = self._alembic_config()
                    config.attributes["connection"] = connection
                    operation(config)
        finally:
            engine.dispose()

    def _alembic_config(self) -> Config:
        config = Config()
        config.set_main_option("script_location", str(self._script_location))
        config.set_main_option("file_template", "%%(year)d%%(month).2d%%(day).2d_%%(rev)s_%%(slug)s")
        config.set_main_option("timezone", "UTC")
        return config

    def _create_engine(self) -> Engine:
        connect_args: dict[str, object]
        if isinstance(self._database, PostgreSQLConfig):
            connect_args = {"connect_timeout": self._database.connect_timeout_seconds}
        else:
            if str(self._database.path) != ":memory:":
                self._database.path.absolute().parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            connect_args = {"timeout": self._database.busy_timeout_seconds}
        return create_engine(
            sync_database_url(self._database),
            poolclass=NullPool,
            connect_args=connect_args,
        )

    def _configure_sqlite(self, connection: Connection) -> None:
        if not isinstance(self._database, SQLiteConfig):
            return
        busy_timeout_ms = _milliseconds(self._database.busy_timeout_seconds)
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql(f"PRAGMA busy_timeout={busy_timeout_ms}")
        connection.exec_driver_sql("PRAGMA synchronous=FULL")
        if str(self._database.path) != ":memory:":
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.commit()

    @contextmanager
    def _migration_lock(self, connection: Connection, *, enabled: bool) -> Generator[None]:
        if not enabled or connection.dialect.name != "postgresql":
            yield
            return

        acquired = False
        operation_error: BaseException | None = None
        try:
            self._acquire_migration_lock(connection)
            acquired = True
            yield
        except BaseException as error:
            operation_error = error
            raise
        finally:
            if acquired:
                try:
                    self._release_migration_lock(connection)
                except SQLAlchemyError as release_error:
                    if operation_error is None:
                        raise
                    operation_error.add_note(f"migration lock cleanup failed with {type(release_error).__name__}")

    def _acquire_migration_lock(self, connection: Connection) -> None:
        self._set_timeout(connection, "lock_timeout", "0")
        self._set_timeout(
            connection,
            "statement_timeout",
            _duration(self._migration.advisory_lock_timeout_seconds),
        )
        self._set_timeout(
            connection,
            "idle_in_transaction_session_timeout",
            _duration(self._migration.idle_transaction_timeout_seconds),
        )
        connection.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": _MIGRATION_LOCK_NAME},
        )
        connection.commit()

        self._set_timeout(
            connection,
            "lock_timeout",
            _duration(self._migration.lock_timeout_seconds),
        )
        self._set_timeout(
            connection,
            "statement_timeout",
            _duration(self._migration.statement_timeout_seconds),
        )
        self._set_timeout(
            connection,
            "idle_in_transaction_session_timeout",
            _duration(self._migration.idle_transaction_timeout_seconds),
        )
        connection.commit()

    @staticmethod
    def _set_timeout(connection: Connection, setting: str, value: str) -> None:
        allowed = {"lock_timeout", "statement_timeout", "idle_in_transaction_session_timeout"}
        if setting not in allowed:
            raise ValueError(f"unsupported PostgreSQL timeout: {setting}")
        connection.execute(
            text("SELECT set_config(:setting, :value, false)"),
            {"setting": setting, "value": value},
        )

    @staticmethod
    def _release_migration_lock(connection: Connection) -> None:
        if connection.in_transaction():
            connection.rollback()
        connection.execute(
            text("SELECT pg_advisory_unlock(hashtextextended(:lock_name, 0))"),
            {"lock_name": _MIGRATION_LOCK_NAME},
        )
        connection.commit()


def _milliseconds(seconds: float) -> int:
    return max(1, round(seconds * 1000))


def _duration(seconds: float) -> str:
    return f"{_milliseconds(seconds)}ms"
