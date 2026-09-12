from pathlib import Path
from uuid import uuid4

import pytest
from a13n_service.database.migration import DatabaseMigrator
from a13n_service.interactions.inbox import ThreadInboxStore
from a13n_service.interactions.models import ThreadRecord
from a13n_service.storage import short_session, transaction
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine, sync_database_url
from alembic import op
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url

from .conftest import NOW, ORGANIZATION_ID, _seed_interaction_database
from .test_attempt_execution import _accept_root
from .test_inbox import _input

PREVIOUS_REVISION = "f6ec04da96ed"
ACCOUNTING_REVISION = "56b4ee866468"


@pytest.fixture(params=["sqlite", "postgresql"])
def accounting_database(request, tmp_path: Path):
    if request.param == "postgresql":
        url = make_url(request.getfixturevalue("pg_url"))
        name = "inbox_accounting_" + uuid4().hex
        engine = create_engine(url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
            try:
                yield PostgreSQLConfig(url=url.set(database=name).render_as_string(hide_password=False))
            finally:
                with engine.connect() as connection:
                    connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        finally:
            engine.dispose()
    else:
        yield SQLiteConfig(path=tmp_path / "inbox-accounting.sqlite3")


@pytest.mark.anyio
async def test_accounting_migration_preserves_pending_entries_and_sequence_history(
    accounting_database, interaction_object_store
):
    config = accounting_database
    migrator = DatabaseMigrator(config)
    migrator.upgrade(ACCOUNTING_REVISION)
    async_engine = create_sql_engine(config)
    sessions = create_session_factory(async_engine)
    try:
        await _seed_interaction_database(sessions)
        _, run, _ = await _accept_root(sessions, interaction_object_store)
        store = ThreadInboxStore(sessions, clock=lambda: NOW)
        payloads = (_input("first retained input"), _input("second retained input"))
        receipts = [
            await store.append_steer(organization_id=ORGANIZATION_ID, run_id=run.id, input=payload)
            for payload in payloads
        ]
        async with transaction(sessions) as database:
            thread = await database.scalar(
                select(ThreadRecord).where(ThreadRecord.id == run.thread_id).with_for_update()
            )
            # The high-water mark must survive independently of retained entries.
            thread.next_delivery_sequence = 91
            original = thread.to_resource().model_dump(mode="json")
    finally:
        await async_engine.dispose()

    expected = (91, 2, sum(len(payload.canonical_bytes()) for payload in payloads))
    engine = create_engine(sync_database_url(config))
    try:
        # Recreate a real previous-version database with pending inbox and Run
        # references, then prove that upgrading copies all three values back.
        migrator.downgrade(PREVIOUS_REVISION)
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT next_delivery_sequence, pending_count, pending_bytes FROM thread_inbox_counters")
            ).one()
            assert tuple(row) == expected
        migrator.upgrade(ACCOUNTING_REVISION)
        migrator.current(check_heads=True, verbose=False)
        assert "thread_inbox_counters" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert (
                tuple(
                    connection.execute(
                        text("SELECT next_delivery_sequence, pending_count, pending_bytes FROM threads")
                    ).one()
                )
                == expected
            )
            assert set(connection.scalars(text("SELECT id FROM thread_inbox"))) == {
                receipt.steer_id for receipt in receipts
            }
            assert connection.scalar(text("SELECT count(*) FROM runs")) == 1
            if isinstance(config, SQLiteConfig):
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        engine.dispose()

    async_engine = create_sql_engine(config)
    sessions = create_session_factory(async_engine)
    try:
        async with short_session(sessions) as database:
            thread = await database.get(ThreadRecord, run.thread_id)
            assert thread.to_resource().model_dump(mode="json") == original
        next_receipt = await ThreadInboxStore(sessions, clock=lambda: NOW).append_steer(
            organization_id=ORGANIZATION_ID, run_id=run.id, input=_input("after upgrade")
        )
        assert next_receipt.delivery_sequence == 91
    finally:
        await async_engine.dispose()


def test_missing_legacy_accounting_fails_before_schema_changes(accounting_database):
    config = accounting_database
    migrator = DatabaseMigrator(config)
    migrator.upgrade(PREVIOUS_REVISION)
    engine = create_engine(sync_database_url(config))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO organizations (id, key, name, created_at, updated_at) VALUES ('org', 'org', 'Org', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO workspaces (id, organization_id, key, name, created_at, updated_at) VALUES ('ws', 'org', 'ws', 'Workspace', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO sessions (id, organization_id, workspace_id, created_at, updated_at) VALUES ('session', 'org', 'ws', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO threads (id, organization_id, session_id, version, queue_version, role, origin_kind, created_at, updated_at) VALUES ('thread', 'org', 'session', 1, 0, 'root', 'new', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
        with pytest.raises(RuntimeError, match="counter is missing"):
            migrator.upgrade(ACCOUNTING_REVISION)
        assert "thread_inbox_counters" in inspect(engine).get_table_names()
        assert "next_delivery_sequence" not in {column["name"] for column in inspect(engine).get_columns("threads")}
    finally:
        engine.dispose()


def test_interrupted_accounting_migration_rolls_back_and_can_retry(accounting_database, monkeypatch):
    config = accounting_database
    migrator = DatabaseMigrator(config)
    migrator.upgrade(PREVIOUS_REVISION)
    drop_table = op.drop_table

    def fail_before_drop(name, *args, **kwargs):
        if name == "thread_inbox_counters":
            raise RuntimeError("interrupted after backfill")
        return drop_table(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(op, "drop_table", fail_before_drop)
        with pytest.raises(RuntimeError, match="interrupted after backfill"):
            migrator.upgrade(ACCOUNTING_REVISION)
    engine = create_engine(sync_database_url(config))
    try:
        assert "thread_inbox_counters" in inspect(engine).get_table_names()
        assert "next_delivery_sequence" not in {column["name"] for column in inspect(engine).get_columns("threads")}
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_REVISION
        migrator.upgrade(ACCOUNTING_REVISION)
        migrator.current(check_heads=True, verbose=False)
    finally:
        engine.dispose()
