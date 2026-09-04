"""The reset OSS baseline must reproduce all current metadata and foreign keys."""

from a13n_service.database import DatabaseMigrator
from a13n_service.database.default_comparison import compare_server_default
from a13n_service.database.metadata import service_metadata
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import sync_database_url
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect


def test_service_baseline_matches_postgresql_metadata(pg_url: str) -> None:
    config = PostgreSQLConfig(url=pg_url)
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    engine = create_engine(sync_database_url(config))
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": compare_server_default}
            )
            metadata = service_metadata()
            assert compare_metadata(context, metadata) == []
            inspector = inspect(connection)
            for table in metadata.tables.values():
                expected = {
                    connection.dialect.identifier_preparer.format_constraint(constraint)
                    for constraint in table.foreign_key_constraints
                }
                actual = {item["name"] for item in inspector.get_foreign_keys(table.name)}
                assert actual == expected, table.name
            assert "connectors" not in inspector.get_table_names()
            columns = {item["name"] for item in inspector.get_columns("connector_providers")}
            assert {"type", "configuration_json"} <= columns
            assert {"endpoint", "driver_key", "config_version", "config_json"}.isdisjoint(columns)
    finally:
        engine.dispose()
        migrator.downgrade("base")
