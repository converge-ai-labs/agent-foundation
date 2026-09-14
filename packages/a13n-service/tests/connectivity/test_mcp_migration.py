from pathlib import Path

from a13n_service.database.metadata import service_metadata
from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

TABLES = {"connections", "connection_authorizations", "mcp_oauth_clients"}


def _exercise(config: PostgreSQLConfig | SQLiteConfig) -> None:
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    engine = create_engine(sync_database_url(config))
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "include_object": lambda obj, name, kind, reflected, compare_to: kind != "table" or name in TABLES,
                },
            )
            assert compare_metadata(context, service_metadata()) == []
        inspector = inspect(engine)
        assert "mcp_tool_catalogs" not in inspector.get_table_names()
        assert TABLES <= set(inspector.get_table_names())
        connection_columns = {column["name"] for column in inspector.get_columns("connections")}
        assert {
            "endpoint_url",
            "auth_mode",
            "credential_generation",
            "refresh_claim_generation",
            "refresh_claim_owner",
            "refresh_claim_expires_at",
            "deleted_at",
        } <= connection_columns
        assert {"owner_user_id", "refresh_available_at", "cleanup_pending", "cleanup_attempt_count"}.isdisjoint(
            connection_columns
        )
        constraints = {constraint["name"] for constraint in inspector.get_check_constraints("connections")}
        assert "ck_connections_credential_generations_valid" in constraints
        session_constraints = inspector.get_check_constraints("connection_authorizations")
        assert any("received" in constraint["sqltext"] for constraint in session_constraints)
        session_columns = {column["name"] for column in inspector.get_columns("connection_authorizations")}
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
