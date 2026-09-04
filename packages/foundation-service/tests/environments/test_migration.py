from pathlib import Path

from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect

ENVIRONMENT_TABLES = {
    "environment_providers",
    "environment_templates",
    "environments",
    "environment_commands",
    "environment_template_revisions",
}


def _assert_tables(config: PostgreSQLConfig | SQLiteConfig, *, present: bool) -> None:
    engine = create_engine(sync_database_url(config))
    try:
        tables = set(inspect(engine).get_table_names())
        if present:
            assert ENVIRONMENT_TABLES <= tables
            revision_columns = {
                column["name"] for column in inspect(engine).get_columns("environment_template_revisions")
            }
            assert {"recipe", "template_id", "provider_id"} <= revision_columns
            assert "provider" not in revision_columns
        else:
            assert ENVIRONMENT_TABLES.isdisjoint(tables)
    finally:
        engine.dispose()


def _exercise_migration(config: PostgreSQLConfig | SQLiteConfig) -> None:
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    _assert_tables(config, present=True)
    migrator.downgrade("base")
    _assert_tables(config, present=False)


def test_environment_schema_migrates_up_and_down_on_sqlite(tmp_path: Path) -> None:
    _exercise_migration(SQLiteConfig(path=tmp_path / "environment-migrations.sqlite3"))


def test_environment_schema_migrates_up_and_down_on_postgresql(pg_url: str) -> None:
    _exercise_migration(PostgreSQLConfig(url=pg_url))
