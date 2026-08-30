from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_ui.settings import StorageSettings
from a13n_ui.storage.database import open_database, short_session, transaction
from a13n_ui.storage.metadata import agent_ui_metadata
from a13n_ui.storage.migration import DatabaseMigrator, DatabaseSchemaError
from a13n_ui.storage.models import RecoveryDiagnosticRecord
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
            "alembic_version",
            "composition_snapshot",
            "configuration_diagnostic",
            "configuration_generation",
            "current_configuration",
            "event_segment",
            "generation_resource",
            "host_environment_resource",
            "immutable_object",
            "item_projection",
            "local_session",
            "pending_deferred",
            "pending_submission",
            "recovery_diagnostic",
            "resource_revision",
            "session_environment_assignment",
            "session_presentation",
            "session_thread",
            "skill_package_reference",
            "thread_checkpoint",
            "thread_turn",
            "turn_run",
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
                RecoveryDiagnosticRecord(
                    process_generation="process-test",
                    code="committed",
                    detail="committed transaction",
                    recorded_at=now,
                )
            )

        with pytest.raises(RuntimeError, match="rollback"):
            async with transaction(database.sessions) as session:
                session.add(
                    RecoveryDiagnosticRecord(
                        process_generation="process-test",
                        code="rollback",
                        detail="rolled back transaction",
                        recorded_at=now,
                    )
                )
                raise RuntimeError("rollback")

        async with short_session(database.sessions) as session:
            codes = set((await session.execute(select(RecoveryDiagnosticRecord.code))).scalars())
            assert codes == {"committed"}
