from pathlib import Path

from a13n_service.database import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect, text

PLUGIN_TABLES = {
    "plugins",
    "plugin_versions",
    "plugin_runtime_state",
    "plugin_runtime_locks",
    "plugin_runtime_tasks",
    "plugin_runtime_resolutions",
}


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


def _exercise_existing_runtime_state_upgrade(config: PostgreSQLConfig | SQLiteConfig) -> None:
    migrator = DatabaseMigrator(config)
    migrator.upgrade("4d180002f87e")
    engine = create_engine(sync_database_url(config))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO plugin_runtime_state "
                    "(id, mode, active_lock_digest, version, created_at, updated_at) "
                    "VALUES ('runtime', 'runner', NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
    finally:
        engine.dispose()

    migrator.upgrade()
    engine = create_engine(sync_database_url(config))
    try:
        with engine.connect() as connection:
            generation = connection.scalar(
                text("SELECT command_claim_generation FROM plugin_runtime_state WHERE id = 'runtime'")
            )
            assert generation == 0
    finally:
        engine.dispose()
    migrator.downgrade("base")


def test_plugin_schema_migrates_up_and_down_on_sqlite(tmp_path: Path) -> None:
    _exercise_migration(SQLiteConfig(path=tmp_path / "plugin-migrations.sqlite3"))


def test_plugin_schema_migrates_up_and_down_on_postgresql(pg_url: str) -> None:
    _exercise_migration(PostgreSQLConfig(url=pg_url))


def test_plugin_runtime_task_migration_upgrades_existing_state_on_sqlite(tmp_path: Path) -> None:
    _exercise_existing_runtime_state_upgrade(SQLiteConfig(path=tmp_path / "plugin-state-upgrade.sqlite3"))


def test_plugin_runtime_task_migration_upgrades_existing_state_on_postgresql(pg_url: str) -> None:
    _exercise_existing_runtime_state_upgrade(PostgreSQLConfig(url=pg_url))
