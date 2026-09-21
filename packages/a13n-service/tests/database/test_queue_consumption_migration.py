"""Queue request keys survive transfer to retained, including sealed, Runs."""

import pytest
from a13n_service.database import DatabaseMigrator
from a13n_service.interactions.control_domain import ConsumeQueuedSubmissionRequest, InterruptRequest
from a13n_service.storage.relational import database_url
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from tests.gateway.conftest import lifecycle_interaction_sessions as lifecycle_interaction_sessions
from tests.gateway.test_commands import _actor
from tests.gateway.test_queue import _consumable_queue
from tests.interactions.conftest import interaction_sessions as interaction_sessions

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("conflicting_key", [False, True])
async def test_queue_consumption_key_upgrade_preserves_retained_result(
    service_database, lifecycle_interaction_sessions, tmp_path, conflicting_key
):
    service, commands, source, _queued = await _consumable_queue(lifecycle_interaction_sessions, tmp_path)
    kwargs = dict(
        actor=_actor(),
        thread_id=source.thread_id,
        request=ConsumeQueuedSubmissionRequest(expected_thread_version=2, expected_queue_version=1),
        idempotency_key="retained-consumption",
    )
    accepted = await service.consume(**kwargs)
    assert accepted.run is not None
    await commands.active.interrupt(
        actor=_actor(),
        run_id=accepted.run.run_id,
        request=InterruptRequest(expected_thread_version=3, expected_run_version=1),
        idempotency_key="seal-consumed-run",
    )
    migrator = DatabaseMigrator(service_database)
    engine = create_engine(database_url(service_database))
    try:
        # Downgrade reconstructs the old queue-owned key without losing a result.
        migrator.downgrade("7c46b77d7cf1")
        with engine.begin() as connection:
            old_key = connection.scalar(
                text("SELECT consumption_key FROM thread_queued_submissions WHERE consumed_run_id = :id"),
                {"id": accepted.run.run_id},
            )
            assert old_key is not None
            connection.execute(text("ALTER TABLE runs DISABLE TRIGGER reject_sealed_run_update"))
            connection.execute(
                text("UPDATE runs SET request_key = :key WHERE id = :id"),
                {"id": accepted.run.run_id, "key": "f" * 64 if conflicting_key else None},
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            connection.execute(text("ALTER TABLE runs ENABLE TRIGGER reject_sealed_run_update"))

        if conflicting_key:
            with pytest.raises(RuntimeError, match="conflict with retained Run keys"):
                migrator.upgrade()
            with engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT consumption_key FROM thread_queued_submissions WHERE consumed_run_id = :id"),
                        {"id": accepted.run.run_id},
                    )
                    == old_key
                )
                assert (
                    connection.scalar(text("SELECT request_key FROM runs WHERE id = :id"), {"id": accepted.run.run_id})
                    == "f" * 64
                )
        else:
            migrator.upgrade()
            with engine.connect() as connection:
                assert "consumption_key" not in {
                    column["name"] for column in inspect(connection).get_columns("thread_queued_submissions")
                }
                assert (
                    connection.scalar(text("SELECT request_key FROM runs WHERE id = :id"), {"id": accepted.run.run_id})
                    == old_key
                )
            replay = await service.consume(**kwargs)
            assert replay.run is not None
            assert replay.run.run_id == accepted.run.run_id
            assert replay.run.run_version == 2 and replay.run.thread_version == 4

        # Both success and rolled-back failure leave the sealed-row guard enabled.
        with pytest.raises(DBAPIError, match="sealed"):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE runs SET version = version + 1 WHERE id = :id"), {"id": accepted.run.run_id}
                )
    finally:
        engine.dispose()
