from pathlib import Path

import pytest
from converge_foundation_service.database.cli import MigrationCommandSettings
from converge_foundation_service.database.config import MigrationConfig
from converge_foundation_service.storage.config import PostgreSQLConfig, SQLiteConfig
from pydantic import ValidationError


def test_migration_config_rejects_unbounded_timeouts() -> None:
    with pytest.raises(ValidationError):
        MigrationConfig(statement_timeout_seconds=0)


def test_command_settings_require_explicit_backend() -> None:
    with pytest.raises(ValidationError, match="FOUNDATION_DATABASE_BACKEND"):
        MigrationCommandSettings(_env_file=None)


def test_command_settings_build_postgresql_config() -> None:
    settings = MigrationCommandSettings(
        _env_file=None,
        database_backend="postgresql",
        database_url="postgresql://foundation:secret@example.test/foundation",
    )

    database = settings.database_config()
    assert isinstance(database, PostgreSQLConfig)
    assert "secret" not in repr(database)


def test_command_settings_build_sqlite_config(tmp_path: Path) -> None:
    path = tmp_path / "foundation.sqlite3"
    settings = MigrationCommandSettings(
        _env_file=None,
        database_backend="sqlite",
        database_sqlite_path=path,
    )

    database = settings.database_config()
    assert isinstance(database, SQLiteConfig)
    assert database.path == path
