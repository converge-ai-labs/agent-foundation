"""The domain baseline chain must preserve dependencies and the final schema."""

from a13n_service.database import DatabaseMigrator
from a13n_service.database.default_comparison import compare_server_default
from a13n_service.database.metadata import service_metadata
from a13n_service.database.migration import MIGRATIONS_PATH
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import database_url
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

_NAME_KEY_TABLES = {
    "application_accounts",
    "connector_connections",
    "connector_providers",
    "mcp_connections",
    "model_providers",
    "service_accounts",
}

_BOUNDED_COLUMNS = {table: ("normalized_name", 384) for table in _NAME_KEY_TABLES}
_BOUNDED_COLUMNS.update({table: ("key", 64) for table in ("organizations", "workspaces", "agents")})
_BOUNDED_COLUMNS["assets"] = ("filename", 256)


def test_domain_baselines_upgrade_and_downgrade_at_every_boundary(postgres_database: PostgreSQLConfig) -> None:
    config = postgres_database
    history = ScriptDirectory(str(MIGRATIONS_PATH))
    assert len(history.get_bases()) == len(history.get_heads()) == 1
    revisions = list(reversed(list(history.walk_revisions())))
    migrator = DatabaseMigrator(config)
    engine = create_engine(database_url(config))
    snapshots: list[set[str]] = [set()]
    try:
        for revision in revisions:
            migrator.upgrade(revision.revision)
            inspector = inspect(engine)
            tables = set(inspector.get_table_names()) - {"alembic_version"}
            assert {"connector_tool_catalogs", "mcp_tool_catalogs"}.isdisjoint(tables)
            for table in tables & {"runs", "connector_connections", "mcp_connections"}:
                columns = {column["name"] for column in inspector.get_columns(table)}
                assert not any("catalog" in column or column.startswith("mcp_tool_snapshot_") for column in columns)
            for table in tables:
                if table in _BOUNDED_COLUMNS:
                    columns = {column["name"]: column for column in inspector.get_columns(table)}
                    name, width = _BOUNDED_COLUMNS[table]
                    assert columns[name]["type"].length == width, (revision.revision, table, name)
                for foreign_key in inspector.get_foreign_keys(table):
                    assert foreign_key["referred_table"] in tables, (revision.revision, table, foreign_key)
            snapshots.append(tables)
        assert snapshots[-1] == set(service_metadata().tables)
        for index in range(len(revisions) - 1, -1, -1):
            target = revisions[index - 1].revision if index else "base"
            migrator.downgrade(target)
            assert set(inspect(engine).get_table_names()) - {"alembic_version"} == snapshots[index]
        migrator.upgrade()
        migrator.current(check_heads=True, verbose=False)
    finally:
        engine.dispose()
        migrator.downgrade("base")


def test_service_baseline_matches_postgresql_metadata(postgres_database: PostgreSQLConfig) -> None:
    config = postgres_database
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    engine = create_engine(database_url(config))
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
            for name in (
                "model_providers",
                "connector_providers",
                "application_accounts",
                "connections",
                "connection_authorizations",
            ):
                columns = {item["name"] for item in inspector.get_columns(name)}
                assert {"ciphertext", "nonce", "encryption_key_id", "credential_generation"} <= columns
                assert {
                    "credential_version",
                    "credential_secret_id",
                    "setup_secret_id",
                    "setup_secret_generation",
                }.isdisjoint(columns)
                assert all(key["referred_table"] != "secrets" for key in inspector.get_foreign_keys(name))

    finally:
        engine.dispose()
        migrator.downgrade("base")
