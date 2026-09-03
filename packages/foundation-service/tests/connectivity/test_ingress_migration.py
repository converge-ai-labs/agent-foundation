from pathlib import Path

from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect

TABLES = {"connectivity_commands", "ingresses", "ingress_agents", "ingress_routes"}


def _exercise(config: PostgreSQLConfig | SQLiteConfig) -> None:
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    engine = create_engine(sync_database_url(config))
    try:
        inspector = inspect(engine)
        assert TABLES <= set(inspector.get_table_names())
        command_columns = {column["name"] for column in inspector.get_columns("connectivity_commands")}
        assert "idempotency_key_digest" in command_columns
        assert "idempotency_key" not in command_columns
    finally:
        engine.dispose()
    migrator.downgrade("base")
    engine = create_engine(sync_database_url(config))
    try:
        assert TABLES.isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_ingress_schema_migrates_on_sqlite(tmp_path: Path) -> None:
    _exercise(SQLiteConfig(path=tmp_path / "connectivity-migrations.sqlite3"))


def test_ingress_schema_migrates_on_postgresql(pg_url: str) -> None:
    _exercise(PostgreSQLConfig(url=pg_url))
