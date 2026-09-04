from pathlib import Path

from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect

TABLES = {"mcp_connections", "mcp_oauth_sessions", "mcp_tool_catalogs"}
CONNECTIVITY_PARENT_REVISION = "93f7e255236d"


def _exercise(config: PostgreSQLConfig | SQLiteConfig) -> None:
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    engine = create_engine(sync_database_url(config))
    try:
        inspector = inspect(engine)
        assert TABLES <= set(inspector.get_table_names())
        connection_columns = {column["name"] for column in inspector.get_columns("mcp_connections")}
        assert {
            "endpoint_url",
            "auth_mode",
            "owner_user_id",
            "credential_generation",
            "catalog_generation",
            "current_catalog_digest",
            "deleted_at",
        } <= connection_columns
        session_columns = {column["name"] for column in inspector.get_columns("mcp_oauth_sessions")}
        assert {"state_digest", "setup_secret_id", "claim_generation", "expires_at"} <= session_columns
    finally:
        engine.dispose()
    migrator.downgrade(CONNECTIVITY_PARENT_REVISION)
    engine = create_engine(sync_database_url(config))
    try:
        assert TABLES.isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    migrator.downgrade("base")


def test_mcp_schema_migrates_on_sqlite(tmp_path: Path) -> None:
    _exercise(SQLiteConfig(path=tmp_path / "mcp-migrations.sqlite3"))


def test_mcp_schema_migrates_on_postgresql(pg_url: str) -> None:
    _exercise(PostgreSQLConfig(url=pg_url))
