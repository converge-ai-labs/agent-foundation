"""Data preservation and rollback for consolidation of Run execution facts."""

from pathlib import Path

import pytest
from a13n_service.database.migration import DatabaseMigrator
from a13n_service.interactions.attempts import AttemptExecutionService
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.storage import short_session
from a13n_service.storage.config import PostgreSQLConfig, SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine, sync_database_url
from a13n_service.subagents.acceptance import ChildRunAcceptanceService
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import DBAPIError

from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, ORGANIZATION_ID, _seed_interaction_database, effective_agent_config
from .test_attempt_execution import _authority, _completed_state, _worker
from .test_subagent_acceptance import _accept_parent, _grant_and_seed_child, _prepared_child

pytestmark = pytest.mark.anyio
_PREVIOUS = "8560ee8dfdab"
_CURRENT = "4621c7df5e77"


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
async def test_execution_identity_migration_preserves_populated_history(
    request, tmp_path, interaction_object_store, backend
):
    config = (
        SQLiteConfig(path=tmp_path / "populated.sqlite3")
        if backend == "sqlite"
        else PostgreSQLConfig(url=request.getfixturevalue("pg_url"))
    )
    migrator = DatabaseMigrator(config)
    migrator.upgrade()
    engine = create_sql_engine(config)
    sessions = create_session_factory(engine)
    try:
        await _seed_interaction_database(sessions)
        await _grant_and_seed_child(sessions)
        states, parent, initial = await _accept_parent(sessions, interaction_object_store)
        claim = await AttemptScheduler(sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
            parent.id, _worker()
        )
        assert isinstance(claim, ClaimedAttempt)
        authority = _authority(claim)
        execution = AttemptExecutionService(sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
        prepared = await execution.commit_preparation_success(authority)
        await execution.enter_harness(authority, preparation=prepared, harness_run_id="migration-parent")
        await execution.increment_model_request(authority)
        async with short_session(sessions) as session:
            record = await session.get(RunRecord, parent.id)
            running = record.to_resource()
        child = _prepared_child(
            running, initial, authority.run_attempt_id, authority.attempt_number, effective_agent_config(), suffix="7"
        )
        await ChildRunAcceptanceService(
            sessions, states, RunPayloadStore(interaction_object_store), clock=lambda: NOW
        ).accept(child, authority)
        stored = await execution.publish_checkpoint(
            authority,
            states,
            await states.read(ORGANIZATION_ID, parent.id),
            _completed_state(initial, authority.run_attempt_id, authority.attempt_number),
        )
        await RunOutcomeService(
            sessions, RunPayloadStore(interaction_object_store), clock=lambda: NOW, lifecycle=test_lifecycle_writer()
        ).commit_state_outcome(authority, stored)
    finally:
        await engine.dispose()

    sync = create_engine(sync_database_url(config))
    try:
        with sync.connect() as connection:
            before = _history(connection)
        migrator.downgrade(_PREVIOUS)
        with sync.connect() as connection:
            row = connection.execute(text("SELECT * FROM run_attempts")).mappings().one()
            assert row["fence"] == row["attempt_number"] == 1
            assert row["claimed_at"] == row["created_at"]
            assert row["worker_generation"] == row["worker_id"]
        with sync.begin() as connection:
            connection.execute(text("UPDATE runs SET next_attempt_fence = 2 WHERE id = :id"), {"id": child.run.id})
        with pytest.raises(RuntimeError, match="identity facts differ"):
            migrator.upgrade()
        with sync.begin() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == _PREVIOUS
            connection.execute(text("UPDATE runs SET next_attempt_fence = 1 WHERE id = :id"), {"id": child.run.id})
        migrator.upgrade()
        with sync.connect() as connection:
            assert _history(connection) == before
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == _CURRENT
            if backend == "sqlite":
                assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        for table in ("runs", "run_attempts"):
            with pytest.raises(DBAPIError), sync.begin() as connection:
                connection.execute(
                    text(f"UPDATE {table} SET version = version + 1 WHERE status = 'succeeded' OR status = 'completed'")
                )
    finally:
        sync.dispose()
        migrator.downgrade("base")


def _history(connection):
    return {
        table: list(connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings())
        for table in ("runs", "run_attempts", "child_run_relationships", "threads")
    }


def test_sqlite_revision_stamp_failure_rolls_back_schema_and_can_retry(tmp_path: Path, monkeypatch):
    config = SQLiteConfig(path=tmp_path / "interrupted.sqlite3")
    migrator = DatabaseMigrator(config)
    migrator.upgrade(_PREVIOUS)
    engine = create_engine(sync_database_url(config))
    fail_stamp = True

    @event.listens_for(engine, "before_cursor_execute")
    def interrupt(connection, cursor, statement, parameters, context, executemany):
        if fail_stamp and statement.startswith("UPDATE alembic_version"):
            raise RuntimeError("interrupted before revision stamp")

    monkeypatch.setattr(migrator, "_create_engine", lambda: engine)
    with pytest.raises(RuntimeError, match="interrupted before revision stamp"):
        migrator.upgrade()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == _PREVIOUS
        assert "worker_generation" in {item["name"] for item in inspect(connection).get_columns("run_attempts")}
        assert "next_attempt_fence" in {item["name"] for item in inspect(connection).get_columns("runs")}
        assert (
            connection.execute(
                text("SELECT count(*) FROM sqlite_master WHERE name = 'reject_sealed_run_update'")
            ).scalar_one()
            == 1
        )
    fail_stamp = False
    migrator.upgrade()
    migrator.current(check_heads=True, verbose=False)
    migrator.downgrade("base")
