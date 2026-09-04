from pathlib import Path

from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect

TABLES = {
    "connector_connection_operations",
    "connector_connections",
    "connector_setup_attempts",
    "connector_tool_catalogs",
    "connector_providers",
}


def _exercise(configuration: PostgreSQLConfig | SQLiteConfig) -> None:
    migrator = DatabaseMigrator(configuration)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    engine = create_engine(sync_database_url(configuration))
    try:
        inspector = inspect(engine)
        assert TABLES <= set(inspector.get_table_names())
        connection_columns = {column["name"] for column in inspector.get_columns("connector_connections")}
        assert {
            "external_ref",
            "setup_generation",
            "revoke_generation",
            "catalog_generation",
            "current_catalog_digest",
            "deleted_at",
        } <= connection_columns
        attempt_columns = {column["name"] for column in inspector.get_columns("connector_setup_attempts")}
        assert "external_user_correlation" in attempt_columns
        assert "redirect_url" not in attempt_columns
        catalog_columns = {column["name"] for column in inspector.get_columns("connector_tool_catalogs")}
        assert {"digest_sha256", "object_key", "connector_credential_generation"} <= catalog_columns
    finally:
        engine.dispose()
    migrator.downgrade("base")
    engine = create_engine(sync_database_url(configuration))
    try:
        assert TABLES.isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_connector_schema_migrates_on_sqlite(tmp_path: Path) -> None:
    _exercise(SQLiteConfig(path=tmp_path / "connector-migrations.sqlite3"))


def test_connector_schema_migrates_on_postgresql(pg_url: str) -> None:
    _exercise(PostgreSQLConfig(url=pg_url))
