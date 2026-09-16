from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import database_url
from sqlalchemy import create_engine, inspect

AGENT_TABLES = {"agents", "agent_revisions"}


def _assert_tables(config: PostgreSQLConfig, *, present: bool) -> None:
    engine = create_engine(database_url(config))
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
            assert {"connection_tools"} <= revision_columns.keys()
            assert revision_columns["connection_tools"]["default"] is not None
            assert revision_columns["connection_tools"]["default"] is not None
        else:
            assert AGENT_TABLES.isdisjoint(tables)
    finally:
        engine.dispose()


def _exercise_migration(config: PostgreSQLConfig) -> None:
    migrator = DatabaseMigrator(config)

    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    _assert_tables(config, present=True)

    migrator.downgrade("base")
    _assert_tables(config, present=False)


def test_agent_schema_migrates_up_and_down(postgres_database: PostgreSQLConfig) -> None:
    _exercise_migration(postgres_database)
