from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_ui.settings import StorageSettings
from a13n_ui.storage.database import open_database, short_session, transaction
from a13n_ui.storage.metadata import agent_ui_metadata
from a13n_ui.storage.migration import MIGRATIONS_PATH, DatabaseMigrator, DatabaseSchemaError
from a13n_ui.storage.models import ConfigurationDiagnosticRecord
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select, text

pytestmark = pytest.mark.anyio


def test_migration_history_clean_upgrade_and_schema_parity(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    migrator = DatabaseMigrator(path)

    migrator.verify_history()
    migrator.upgrade()
    migrator.verify_current()

    engine = create_engine(f"sqlite:///{path}")
    try:
        assert set(inspect(engine).get_table_names()) == {
            "accepted_configuration",
            "alembic_version",
            "child_execution",
            "child_thread",
            "composition_snapshot",
            "configuration_diagnostic",
            "configuration_snapshot",
            "current_configuration",
            "environment_binding",
            "local_session",
        }
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True, "render_as_batch": True},
            )
            assert compare_metadata(context, agent_ui_metadata()) == []
    finally:
        engine.dispose()


def test_environment_access_migration_upgrades_existing_rows(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            config = Config()
            config.set_main_option("script_location", str(MIGRATIONS_PATH))
            config.attributes["connection"] = connection
            command.upgrade(config, "55b7c7d359aa")
            connection.execute(
                text(
                    """
                    INSERT INTO session_environment_resource (
                        session_id, mount_name, model_alias, permission_ceiling_json,
                        provider_key, provider_schema_version, provider_spec_digest,
                        provider_parameters_json, resource_allocation, status, updated_at
                    ) VALUES (
                        'session-old', 'mount-main', 'workspace', '[\"files\"]',
                        'a13n.direct-local', '1', :digest, '{}',
                        'single_from_spec', 'available', :updated_at
                    )
                    """
                ),
                {"digest": "0" * 64, "updated_at": "2026-08-31 00:00:00"},
            )
            connection.commit()
            command.upgrade(config, "9958168d49de")

            columns = {column["name"] for column in inspect(connection).get_columns("session_environment_resource")}
            access = connection.execute(text("SELECT access FROM session_environment_resource")).scalar_one()

        assert "access" in columns
        assert "permission_ceiling_json" not in columns
        assert access == "read_write"
    finally:
        engine.dispose()


def test_migration_verification_rejects_an_unknown_database_revision(tmp_path: Path) -> None:
    path = tmp_path / "metadata.sqlite3"
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('unknown')"))
    finally:
        engine.dispose()

    with pytest.raises(DatabaseSchemaError, match="found: unknown"):
        DatabaseMigrator(path).verify_current()


async def test_database_configures_sqlite_and_short_transactions(tmp_path: Path) -> None:
    settings = StorageSettings(data_root=tmp_path, busy_timeout_seconds=1.25)
    path = tmp_path / "metadata.sqlite3"

    async with open_database(path, settings) as database:
        async with database.engine.connect() as connection:
            assert (await connection.execute(text("PRAGMA journal_mode"))).scalar_one() == "wal"
            assert (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
            assert (await connection.execute(text("PRAGMA busy_timeout"))).scalar_one() == 1250
            assert (await connection.execute(text("PRAGMA synchronous"))).scalar_one() == 2

        now = datetime.now(UTC)
        async with transaction(database.sessions) as session:
            session.add(
                ConfigurationDiagnosticRecord(
                    process_generation="process-test",
                    code="committed",
                    detail="committed transaction",
                    recorded_at=now,
                )
            )

        with pytest.raises(RuntimeError, match="rollback"):
            async with transaction(database.sessions) as session:
                session.add(
                    ConfigurationDiagnosticRecord(
                        process_generation="process-test",
                        code="rollback",
                        detail="rolled back transaction",
                        recorded_at=now,
                    )
                )
                raise RuntimeError("rollback")

        async with short_session(database.sessions) as session:
            codes = set((await session.execute(select(ConfigurationDiagnosticRecord.code))).scalars())
            assert codes == {"committed"}
