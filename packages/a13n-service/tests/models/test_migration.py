from a13n_service.database.metadata import service_metadata
from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import database_url
from sqlalchemy import create_engine, inspect

MODEL_TABLES = {"model_providers", "models"}


def _assert_schema(configuration: PostgreSQLConfig, *, present: bool) -> None:
    engine = create_engine(database_url(configuration))
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if present:
            assert MODEL_TABLES <= tables
            metadata = service_metadata()
            for name in MODEL_TABLES:
                columns = {item["name"] for item in inspector.get_columns(name)}
                assert columns == set(metadata.tables[name].columns.keys())
        else:
            assert MODEL_TABLES.isdisjoint(tables)
    finally:
        engine.dispose()


def _exercise(configuration: PostgreSQLConfig) -> None:
    migrator = DatabaseMigrator(configuration)
    migrator.upgrade("6fd6194d64ec")
    _assert_schema(configuration, present=True)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    _assert_schema(configuration, present=True)
    migrator.downgrade("base")
    _assert_schema(configuration, present=False)


def test_model_schema_migrates_up_and_down(postgres_database: PostgreSQLConfig) -> None:
    _exercise(postgres_database)
