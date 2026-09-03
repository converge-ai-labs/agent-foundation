from pathlib import Path

from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect

MODEL_TABLES = {"model_providers", "models"}


def _assert_schema(config: PostgreSQLConfig | SQLiteConfig, *, present: bool) -> None:
    engine = create_engine(sync_database_url(config))
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if present:
            assert MODEL_TABLES <= tables
            assert "model_revisions" not in tables
            provider_columns = {item["name"] for item in inspector.get_columns("model_providers")}
            assert {"type", "config", "credential_version", "ciphertext", "enabled"} <= provider_columns
            model_columns = {item["name"] for item in inspector.get_columns("models")}
            assert {"key", "provider_id", "upstream_model", "model_apis", "enabled"} <= model_columns
            assert {"version", "current_revision_id"}.isdisjoint(model_columns)
        else:
            assert MODEL_TABLES.isdisjoint(tables)
    finally:
        engine.dispose()


def _exercise(config: PostgreSQLConfig | SQLiteConfig) -> None:
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    _assert_schema(config, present=True)
    migrator.downgrade("base")
    _assert_schema(config, present=False)


def test_model_schema_migrates_up_and_down_on_sqlite(tmp_path: Path) -> None:
    _exercise(SQLiteConfig(path=tmp_path / "model-migrations.sqlite3"))


def test_model_schema_migrates_up_and_down_on_postgresql(pg_url: str) -> None:
    _exercise(PostgreSQLConfig(url=pg_url))
