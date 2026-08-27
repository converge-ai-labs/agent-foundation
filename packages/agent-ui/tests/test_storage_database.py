from __future__ import annotations

from asyncio import CancelledError
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from a13n_ui.settings import StorageSettings
from a13n_ui.storage.database import open_database, short_session, transaction
from a13n_ui.storage.metadata import agent_ui_metadata
from a13n_ui.storage.migration import DatabaseMigrator, DatabaseSchemaError
from a13n_ui.storage.models import StoreLeaseRecord
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
            "generation_resource",
            "immutable_object",
            "recovery_diagnostic",
            "resource_revision",
            "skill_package_reference",
            "store_lease",
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


async def test_transaction_cleanup_preserves_active_cancellation() -> None:
    rollback = AsyncMock(side_effect=ValueError("connection closed"))
    close = AsyncMock(side_effect=ValueError("connection closed"))
    database_transaction = SimpleNamespace(rollback=rollback, commit=AsyncMock())
    session = SimpleNamespace(
        begin=AsyncMock(return_value=database_transaction),
        close=close,
    )
    factory = cast("async_sessionmaker[AsyncSession]", lambda: session)

    with pytest.raises(CancelledError) as cancelled:
        async with transaction(factory):
            raise CancelledError

    assert cancelled.value.__notes__ == [
        "rollback cleanup failed with ValueError",
        "session close cleanup failed with ValueError",
    ]
    rollback.assert_awaited_once_with()
    close.assert_awaited_once_with()


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
                StoreLeaseRecord(
                    singleton_id=1,
                    process_generation="process-test",
                    acquired_at=now,
                    heartbeat_at=now,
                )
            )

        with pytest.raises(RuntimeError, match="rollback"):
            async with transaction(database.sessions) as session:
                lease = await session.get(StoreLeaseRecord, 1)
                assert lease is not None
                lease.process_generation = "process-rollback"
                raise RuntimeError("rollback")

        async with short_session(database.sessions) as session:
            generation = (await session.execute(select(StoreLeaseRecord.process_generation))).scalar_one()
            assert generation == "process-test"
