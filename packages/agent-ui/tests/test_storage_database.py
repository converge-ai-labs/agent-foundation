from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_ui.settings import StorageSettings
from a13n_ui.storage.database import open_database, short_session, transaction
from a13n_ui.storage.metadata import agent_ui_metadata
from a13n_ui.storage.migration import DatabaseMigrator, DatabaseSchemaError
from a13n_ui.storage.models import AcceptedConfigurationRecord
from alembic.autogenerate import compare_metadata
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
                AcceptedConfigurationRecord(
                    source_digest="1" * 64,
                    yaml_digest="2" * 64,
                    document_json="{}",
                    accepted_at=now,
                )
            )

        with pytest.raises(RuntimeError, match="rollback"):
            async with transaction(database.sessions) as session:
                session.add(
                    AcceptedConfigurationRecord(
                        source_digest="3" * 64,
                        yaml_digest="4" * 64,
                        document_json="{}",
                        accepted_at=now,
                    )
                )
                raise RuntimeError("rollback")

        async with short_session(database.sessions) as session:
            records = tuple((await session.execute(select(AcceptedConfigurationRecord))).scalars())
            assert [record.source_digest for record in records] == ["1" * 64]
            assert records[0].accepted_at.tzinfo is UTC
