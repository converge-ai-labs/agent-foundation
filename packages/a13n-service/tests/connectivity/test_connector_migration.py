from datetime import UTC, datetime

from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import database_url
from sqlalchemy import MetaData, create_engine, inspect, select

TABLES = {
    "connections",
    "connection_authorizations",
    "connector_providers",
}


def _exercise(configuration: PostgreSQLConfig) -> None:
    migrator = DatabaseMigrator(configuration)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    engine = create_engine(database_url(configuration))
    try:
        inspector = inspect(engine)
        assert {"connector_tool_catalogs", "connector_connection_operations"}.isdisjoint(inspector.get_table_names())
        assert TABLES <= set(inspector.get_table_names())
        connection_columns = {column["name"] for column in inspector.get_columns("connections")}
        assert {
            "external_ref",
            "setup_generation",
            "deleted_at",
        } <= connection_columns
        assert {"owner_type", "owner_id", "revoke_generation"}.isdisjoint(connection_columns)
        attempt_columns = {column["name"] for column in inspector.get_columns("connection_authorizations")}
        assert "external_user_correlation" in attempt_columns
        assert "redirect_url" not in attempt_columns
    finally:
        engine.dispose()
    migrator.downgrade("base")
    engine = create_engine(database_url(configuration))
    try:
        assert TABLES.isdisjoint(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_connector_schema_migrates(postgres_database: PostgreSQLConfig) -> None:
    _exercise(postgres_database)


def _exercise_populated_upgrade(configuration: PostgreSQLConfig) -> None:
    migrator = DatabaseMigrator(configuration)
    migrator.upgrade("2999349e6c69")
    engine = create_engine(database_url(configuration))
    metadata = MetaData()
    metadata.reflect(engine, only=["organizations", "workspaces", "connector_providers", "connections"])
    organizations, workspaces, providers, connections = (
        metadata.tables[name] for name in ("organizations", "workspaces", "connector_providers", "connections")
    )
    now = datetime.now(UTC)
    identity = {"created_at": now, "updated_at": now}
    resource = {
        **identity,
        "organization_id": "org_migration",
        "workspace_id": "ws_migration",
        "created_by_type": "user",
        "created_by_id": "user_migration",
        "version": 1,
    }
    try:
        with engine.begin() as connection:
            connection.execute(
                organizations.insert().values(id="org_migration", name="Migration", key="migration", **identity)
            )
            connection.execute(
                workspaces.insert().values(
                    id="ws_migration", organization_id="org_migration", name="Migration", key="migration", **identity
                )
            )
            connection.execute(
                providers.insert().values(
                    id="provider_migration",
                    name="Retained provider",
                    normalized_name="retained provider",
                    type="composio",
                    configuration_json={},
                    status="active",
                    credential_generation=1,
                    ciphertext=b"fixture",
                    nonce=b"fixture",
                    encryption_key_id="fixture",
                    **resource,
                )
            )
            connection.execute(
                connections.insert().values(
                    id="connection_migration",
                    kind="connector",
                    connector_provider_id="provider_migration",
                    connector_key="github",
                    name="Retained connection",
                    normalized_name="retained connection",
                    safe_metadata_json={},
                    status="pending",
                    setup_generation=1,
                    **resource,
                )
            )
        migrator.upgrade()
        migrator.upgrade()  # Retrying startup must not change retained identities.
        current = MetaData()
        current.reflect(engine, only=["connector_providers", "connector_shared_setup_claims", "connections"])
        with engine.connect() as connection:
            provider = connection.execute(select(current.tables["connector_providers"])).mappings().one()
            child = connection.execute(select(current.tables["connections"])).mappings().one()
            assert provider["directory_json"] is None
            assert provider["directory_updated_at"] is None
            assert provider["ciphertext"] == b"fixture"
            assert provider["version"] == 1
            assert child["connector_provider_id"] == provider["id"] == "provider_migration"
            assert child["id"] == "connection_migration"
        claims = current.tables["connector_shared_setup_claims"]
        assert {column.name for column in claims.columns} == {
            "id",
            "provider_id",
            "connector_key",
            "configuration_key",
            "credential_generation",
        }
    finally:
        with engine.begin() as connection:
            for table in (connections, providers, workspaces, organizations):
                connection.execute(table.delete())
        engine.dispose()
        migrator.downgrade("base")


def test_connector_directory_upgrade_preserves_rows(postgres_database: PostgreSQLConfig) -> None:
    _exercise_populated_upgrade(postgres_database)
