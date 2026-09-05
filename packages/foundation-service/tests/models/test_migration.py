from datetime import UTC, datetime
from pathlib import Path

from a13n_service.database.migration import DatabaseMigrator
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import sync_database_url
from sqlalchemy import MetaData, Table, create_engine, inspect, select

MODEL_TABLES = {"model_providers", "models"}


def _assert_schema(
    configuration: PostgreSQLConfig | SQLiteConfig, *, present: bool, capability_columns: bool = True
) -> None:
    engine = create_engine(sync_database_url(configuration))
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        if present:
            assert MODEL_TABLES <= tables
            assert "model_revisions" not in tables
            provider_columns = {item["name"] for item in inspector.get_columns("model_providers")}
            assert {"type", "configuration", "credential_generation", "ciphertext", "enabled"} <= provider_columns
            model_columns = {item["name"] for item in inspector.get_columns("models")}
            assert {
                "key",
                "provider_id",
                "upstream_model",
                "model_api",
                "settings",
                "enabled",
            } <= model_columns
            assert (
                ({"profile", "limits"} <= model_columns)
                if capability_columns
                else {"profile", "limits"}.isdisjoint(model_columns)
            )
            assert {"version", "current_revision_id", "model_apis"}.isdisjoint(model_columns)
        else:
            assert MODEL_TABLES.isdisjoint(tables)
    finally:
        engine.dispose()


def _seed_model(configuration: PostgreSQLConfig | SQLiteConfig) -> None:
    engine = create_engine(sync_database_url(configuration))
    now = datetime(2026, 9, 5, tzinfo=UTC)
    try:
        with engine.begin() as connection:
            metadata = MetaData()

            def table(name: str) -> Table:
                return Table(name, metadata, autoload_with=connection)

            connection.execute(
                table("organizations")
                .insert()
                .values(id="org-migration", name="Migration", created_at=now, updated_at=now)
            )
            connection.execute(
                table("workspaces")
                .insert()
                .values(
                    id="ws-migration",
                    organization_id="org-migration",
                    name="Migration",
                    normalized_name="migration",
                    created_at=now,
                    updated_at=now,
                )
            )
            ownership = dict(
                organization_id="org-migration",
                workspace_id="ws-migration",
                created_by_type="user",
                created_by_id="user-migration",
                updated_by_type="user",
                updated_by_id="user-migration",
                created_at=now,
                updated_at=now,
                enabled=True,
            )
            connection.execute(
                table("model_providers")
                .insert()
                .values(
                    **ownership,
                    id="provider-migration",
                    type="openai",
                    name="Migration",
                    normalized_name="migration",
                    configuration={},
                    credential_generation=0,
                )
            )
            connection.execute(
                table("models")
                .insert()
                .values(
                    **ownership,
                    id="model-migration",
                    key="migration",
                    normalized_key="migration",
                    name="Migration",
                    provider_id="provider-migration",
                    upstream_model="gpt-next",
                    model_api="openai.responses",
                    settings={"max_tokens": 8000},
                    profile={"supports_tools": True},
                    limits={"max_output_tokens": 32000},
                )
            )
    finally:
        engine.dispose()


def _assert_model_preserved(configuration: PostgreSQLConfig | SQLiteConfig, *, restored_columns: bool = False) -> None:
    engine = create_engine(sync_database_url(configuration))
    try:
        with engine.connect() as connection:
            model = Table("models", MetaData(), autoload_with=connection)
            row = connection.execute(select(model).where(model.c.id == "model-migration")).mappings().one()
            assert row["settings"] == {"max_tokens": 8000}
            assert row["upstream_model"] == "gpt-next"
            assert row["provider_id"] == "provider-migration"
            if restored_columns:
                assert row["profile"] == row["limits"] == {}
    finally:
        engine.dispose()


def _exercise(configuration: PostgreSQLConfig | SQLiteConfig) -> None:
    migrator = DatabaseMigrator(configuration)
    migrator.upgrade("6fd6194d64ec")
    _assert_schema(configuration, present=True)
    _seed_model(configuration)
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    _assert_schema(configuration, present=True, capability_columns=False)
    _assert_model_preserved(configuration)
    migrator.downgrade("0d5f1ae47708")
    _assert_model_preserved(configuration, restored_columns=True)
    migrator.upgrade()
    _assert_model_preserved(configuration)
    migrator.downgrade("base")
    _assert_schema(configuration, present=False)


def test_model_schema_migrates_up_and_down_on_sqlite(tmp_path: Path) -> None:
    _exercise(SQLiteConfig(path=tmp_path / "model-migrations.sqlite3"))


def test_model_schema_migrates_up_and_down_on_postgresql(pg_url: str) -> None:
    _exercise(PostgreSQLConfig(url=pg_url))
