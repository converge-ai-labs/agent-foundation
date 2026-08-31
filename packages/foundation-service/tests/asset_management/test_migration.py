from pathlib import Path

from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import create_engine, inspect

ASSET_TABLES = {"assets", "idempotency_evidence", "outbox_records"}


def test_asset_schema_migrates_up_and_down_on_sqlite(tmp_path: Path) -> None:
    config = SQLiteConfig(path=tmp_path / "asset-migrations.sqlite3")
    migrator = DatabaseMigrator(config)

    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    engine = create_engine(sync_database_url(config))
    try:
        assert ASSET_TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    migrator.downgrade("base")
    engine = create_engine(sync_database_url(config))
    try:
        assert ASSET_TABLES.isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_asset_schema_migrates_up_and_down_on_postgresql(pg_url: str) -> None:
    config = PostgreSQLConfig(url=pg_url)
    migrator = DatabaseMigrator(config)

    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    engine = create_engine(sync_database_url(config))
    try:
        assert ASSET_TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    migrator.downgrade("base")
    engine = create_engine(sync_database_url(config))
    try:
        assert ASSET_TABLES.isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()
