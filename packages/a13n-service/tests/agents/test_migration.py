from pathlib import Path

from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect

AGENT_TABLES = {"agents", "agent_revisions"}


def _assert_tables(config: PostgreSQLConfig | SQLiteConfig, *, present: bool) -> None:
    engine = create_engine(sync_database_url(config))
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if present:
            assert AGENT_TABLES <= tables
            columns = {column["name"] for column in inspector.get_columns("agents")}
            assert "image_id" in columns
            assert "current_revision_id" in columns
            assert "active_revision_id" not in columns
            revision_columns = {column["name"]: column for column in inspector.get_columns("agent_revisions")}
            assert {"connector_tools", "mcp_tools"} <= revision_columns.keys()
            assert revision_columns["connector_tools"]["default"] is not None
            assert revision_columns["mcp_tools"]["default"] is not None
        else:
            assert AGENT_TABLES.isdisjoint(tables)
    finally:
        engine.dispose()


def _exercise_migration(config: PostgreSQLConfig | SQLiteConfig) -> None:
    migrator = DatabaseMigrator(config)

    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    _assert_tables(config, present=True)

    migrator.downgrade("base")
    _assert_tables(config, present=False)


def test_agent_schema_migrates_up_and_down_on_sqlite(tmp_path: Path) -> None:
    _exercise_migration(SQLiteConfig(path=tmp_path / "agent-agent-migrations.sqlite3"))


def test_agent_schema_migrates_up_and_down_on_postgresql(pg_url: str) -> None:
    _exercise_migration(PostgreSQLConfig(url=pg_url))
