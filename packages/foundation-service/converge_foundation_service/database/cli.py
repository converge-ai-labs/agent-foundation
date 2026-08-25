"""Executable boundary for Foundation Service schema operations."""

from __future__ import annotations

import argparse
from enum import StrEnum
from pathlib import Path

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from converge_foundation_service.storage.config import PostgreSQLConfig, SQLiteConfig

from .config import MigrationConfig
from .migration import DatabaseMigrator


class DatabaseBackend(StrEnum):
    unset = ""
    postgresql = "postgresql"
    sqlite = "sqlite"


class MigrationCommandSettings(BaseSettings):
    """Load migration-only configuration from ``FOUNDATION_*`` variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="FOUNDATION_",
        extra="ignore",
        case_sensitive=False,
    )

    database_backend: DatabaseBackend = DatabaseBackend.unset
    database_url: SecretStr | None = None
    database_sqlite_path: Path = Path("var/foundation.sqlite3")
    database_connect_timeout_seconds: int = Field(default=10, ge=1, le=300)
    database_sqlite_busy_timeout_seconds: float = Field(default=5, gt=0, le=300)
    migration_advisory_lock_timeout_seconds: float = Field(default=900, gt=0, le=86_400)
    migration_lock_timeout_seconds: float = Field(default=3, gt=0, le=3600)
    migration_statement_timeout_seconds: float = Field(default=900, gt=0, le=86_400)
    migration_idle_transaction_timeout_seconds: float = Field(default=30, gt=0, le=3600)

    @model_validator(mode="after")
    def validate_backend(self) -> MigrationCommandSettings:
        if self.database_backend is DatabaseBackend.unset:
            raise ValueError("FOUNDATION_DATABASE_BACKEND must select postgresql or sqlite")
        if self.database_backend is DatabaseBackend.postgresql and self.database_url is None:
            raise ValueError("FOUNDATION_DATABASE_URL is required for the PostgreSQL backend")
        return self

    def database_config(self) -> PostgreSQLConfig | SQLiteConfig:
        if self.database_backend is DatabaseBackend.postgresql:
            assert self.database_url is not None
            return PostgreSQLConfig(
                url=self.database_url,
                connect_timeout_seconds=self.database_connect_timeout_seconds,
            )
        if self.database_backend is DatabaseBackend.sqlite:
            return SQLiteConfig(
                path=self.database_sqlite_path,
                busy_timeout_seconds=self.database_sqlite_busy_timeout_seconds,
            )
        raise ValueError("database backend was not validated")

    def migration_config(self) -> MigrationConfig:
        return MigrationConfig(
            advisory_lock_timeout_seconds=self.migration_advisory_lock_timeout_seconds,
            lock_timeout_seconds=self.migration_lock_timeout_seconds,
            statement_timeout_seconds=self.migration_statement_timeout_seconds,
            idle_transaction_timeout_seconds=self.migration_idle_transaction_timeout_seconds,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m converge_foundation_service.database")
    commands = parser.add_subparsers(dest="command", required=True)

    upgrade = commands.add_parser("upgrade", help="Apply migrations through a revision.")
    upgrade.add_argument("--revision", default="head")

    downgrade = commands.add_parser("downgrade", help="Downgrade to a reviewed revision.")
    downgrade.add_argument("--revision", default="-1")

    current = commands.add_parser("current", help="Show the current database revision.")
    current.add_argument("--check-heads", action="store_true")

    commands.add_parser("history", help="Show the accepted migration history.")

    revision = commands.add_parser("revision", help="Autogenerate a migration revision.")
    revision.add_argument("message")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        settings = MigrationCommandSettings()
    except ValidationError as error:
        parser.error(str(error))

    migrator = DatabaseMigrator(settings.database_config(), settings.migration_config())
    if arguments.command == "upgrade":
        migrator.upgrade(arguments.revision)
        print(f"Database upgraded to {arguments.revision}.")
    elif arguments.command == "downgrade":
        migrator.downgrade(arguments.revision)
        print(f"Database downgraded to {arguments.revision}.")
    elif arguments.command == "current":
        migrator.current(check_heads=arguments.check_heads)
    elif arguments.command == "history":
        migrator.history()
    elif arguments.command == "revision":
        migrator.revision(arguments.message)
        print(f"Migration generated: {arguments.message}")
    else:
        parser.error(f"unknown command: {arguments.command}")
    return 0
