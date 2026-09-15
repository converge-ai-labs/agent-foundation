from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import database_url
from sqlalchemy import create_engine, inspect

ASSET_TABLES = {"assets", "idempotency_evidence", "outbox_records"}


def test_asset_schema_migrates_up_and_down(postgres_database: PostgreSQLConfig) -> None:
    migrator = DatabaseMigrator(postgres_database)

    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    engine = create_engine(database_url(postgres_database))
    try:
        assert ASSET_TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    migrator.downgrade("base")
    engine = create_engine(database_url(postgres_database))
    try:
        assert ASSET_TABLES.isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()
