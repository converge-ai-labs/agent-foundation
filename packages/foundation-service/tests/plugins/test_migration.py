from pathlib import Path

from a13n_service.database import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect

PLUGIN_TABLES = {"plugins", "plugin_versions", "plugin_runtime_state"}


def _assert_tables(config: PostgreSQLConfig | SQLiteConfig, *, present: bool) -> None:
    engine = create_engine(sync_database_url(config))
    try:
        tables = set(inspect(engine).get_table_names())
        if present:
            assert PLUGIN_TABLES <= tables
        else:
            assert PLUGIN_TABLES.isdisjoint(tables)
    finally:
        engine.dispose()


def _exercise_migration(config: PostgreSQLConfig | SQLiteConfig) -> None:
    migrator = DatabaseMigrator(config)

    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    _assert_tables(config, present=True)

    migrator.downgrade("base")
    _assert_tables(config, present=False)


def test_plugin_schema_migrates_up_and_down_on_sqlite(tmp_path: Path) -> None:
    _exercise_migration(SQLiteConfig(path=tmp_path / "plugin-migrations.sqlite3"))


def test_plugin_schema_migrates_up_and_down_on_postgresql(pg_url: str) -> None:
    _exercise_migration(PostgreSQLConfig(url=pg_url))
