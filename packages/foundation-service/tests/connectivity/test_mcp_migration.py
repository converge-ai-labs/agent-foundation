from pathlib import Path

from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect

TABLES = {"mcp_connections", "mcp_oauth_sessions"}


def _exercise(config: PostgreSQLConfig | SQLiteConfig) -> None:
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    engine = create_engine(sync_database_url(config))
    try:
        inspector = inspect(engine)
        assert "mcp_tool_catalogs" not in inspector.get_table_names()
        assert TABLES <= set(inspector.get_table_names())
        connection_columns = {column["name"] for column in inspector.get_columns("mcp_connections")}
        assert {
            "endpoint_url",
            "auth_mode",
            "owner_user_id",
            "credential_generation",
            "refresh_claim_generation",
            "refresh_claim_owner",
            "refresh_claim_expires_at",
            "refresh_available_at",
            "refresh_last_error_code",
            "deleted_at",
        } <= connection_columns
        indexes = {index["name"]: index for index in inspector.get_indexes("mcp_connections")}
        assert indexes["ix_mcp_connections_refresh_reconcile"]["column_names"] == [
            "status",
            "refresh_available_at",
            "refresh_claim_expires_at",
            "id",
        ]
        constraints = {constraint["name"] for constraint in inspector.get_check_constraints("mcp_connections")}
        assert "ck_mcp_connections_refresh_claim_generation_non_negative" in constraints
        session_columns = {column["name"] for column in inspector.get_columns("mcp_oauth_sessions")}
        assert {"state_digest", "ciphertext", "claim_generation", "expires_at"} <= session_columns
    finally:
        engine.dispose()
    migrator.downgrade("base")
    engine = create_engine(sync_database_url(config))
    try:
        assert TABLES.isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_mcp_schema_migrates_on_sqlite(tmp_path: Path) -> None:
    _exercise(SQLiteConfig(path=tmp_path / "mcp-migrations.sqlite3"))


def test_mcp_schema_migrates_on_postgresql(pg_url: str) -> None:
    _exercise(PostgreSQLConfig(url=pg_url))
