"""Resource-owned lifecycle allocation stays atomic across contention and retention."""

import asyncio
from datetime import timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_service.interactions._transitions import seal_failed_run
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.lifecycle import append_lifecycle_event, new_mutation_id
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.storage import short_session, transaction
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from tests.interactions.conftest import NOW, THREAD_ID
from tests.interactions.test_lifecycle import RUN_ID, _draft, _seed_run
from tests.sql_capture import capture_sql

pytestmark = pytest.mark.anyio


async def test_resource_mutation_counter_and_event_share_one_statement(interaction_sessions):
    sessions = interaction_sessions
    await _seed_run(sessions)
    async with transaction(sessions) as database:
        run = await database.get(RunRecord, RUN_ID, with_for_update=True)
        thread = await database.get(ThreadRecord, THREAD_ID)
        failure = SafeFailure(code="test_failure", message="Failed before execution")
        seal_failed_run(run, thread, failure, NOW + timedelta(seconds=1))
        with capture_sql(sessions) as statements:
            event_id = await LifecycleWriter().append_run_lifecycle(
                database, run, "run.failed", occurred_at=NOW, actor_type="system", actor_id=None
            )
            # A later flush must not repeat the mutation already persisted by the CTE.
            await database.flush()
        mutations = [sql for sql in statements if "UPDATE runs " in sql or "INSERT INTO lifecycle_events " in sql]
        assert len(mutations) == 1
        assert "UPDATE runs " in mutations[0] and "INSERT INTO lifecycle_events " in mutations[0]
        assert run.lifecycle_seq == 1

    async with short_session(sessions) as database:
        stored = await database.get(RunRecord, RUN_ID)
        event = await database.scalar(select(LifecycleEventRecord).where(LifecycleEventRecord.id == event_id))
        assert stored.status == "failed" and stored.version == 2 and stored.lifecycle_seq == 1
        assert event.entity_version == stored.version and event.resource_seq == stored.lifecycle_seq
        assert event.payload["failure"]["code"] == "test_failure"


async def test_failed_delivery_writer_rolls_back_resource_counter_and_fact(interaction_sessions):
    sessions = interaction_sessions
    await _seed_run(sessions)

    async def reject_delivery(database, event):
        assert event.resource_seq == 1
        raise RuntimeError("delivery failed")

    with pytest.raises(RuntimeError, match="delivery failed"):
        async with transaction(sessions) as database:
            run = await database.get(RunRecord, RUN_ID, with_for_update=True)
            run.version += 1
            await LifecycleWriter((reject_delivery,)).append_run_lifecycle(
                database, run, "run.accepted", occurred_at=NOW, actor_type="system", actor_id=None
            )

    async with short_session(sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        assert run.version == 1 and run.lifecycle_seq == 0
        assert await database.scalar(select(func.count()).select_from(LifecycleEventRecord)) == 0
    async with transaction(sessions) as database:
        event = await append_lifecycle_event(database, _draft(mutation_id=new_mutation_id()))
        assert event.resource_seq == 1


async def test_counter_survives_removal_of_every_retained_event(interaction_sessions):
    sessions = interaction_sessions
    await _seed_run(sessions)
    async with transaction(sessions) as database:
        for _ in range(2):
            await append_lifecycle_event(database, _draft(mutation_id=new_mutation_id()))
        await database.execute(delete(LifecycleEventRecord))
    async with transaction(sessions) as database:
        event = await append_lifecycle_event(database, _draft(mutation_id=new_mutation_id()))
        assert event.resource_seq == 3
    async with short_session(sessions) as database:
        assert (await database.get(RunRecord, RUN_ID)).lifecycle_seq == 3


async def test_concurrent_counter_allocation_is_contiguous(interaction_sessions):
    sessions = interaction_sessions
    await _seed_run(sessions)

    async def append():
        async with transaction(sessions) as database:
            event = await append_lifecycle_event(database, _draft(mutation_id=new_mutation_id()))
            return event.resource_seq

    sequences = await asyncio.gather(*(append() for _ in range(8)))
    assert sorted(sequences) == list(range(1, 9))
    async with short_session(sessions) as database:
        assert (await database.get(RunRecord, RUN_ID)).lifecycle_seq == 8


async def test_conflicting_event_does_not_consume_a_counter_value(interaction_sessions):
    sessions = interaction_sessions
    await _seed_run(sessions)
    mutation_id = new_mutation_id()
    async with transaction(sessions) as database:
        await append_lifecycle_event(database, _draft(mutation_id=mutation_id))
    with pytest.raises(IntegrityError):
        async with transaction(sessions) as database:
            await append_lifecycle_event(database, _draft(mutation_id=mutation_id))
    async with transaction(sessions) as database:
        event = await append_lifecycle_event(database, _draft(mutation_id=new_mutation_id()))
        assert event.resource_seq == 2


async def test_stale_resource_version_cannot_append_a_fact(interaction_sessions):
    sessions = interaction_sessions
    await _seed_run(sessions)
    async with transaction(sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        await database.execute(
            update(RunRecord)
            .where(RunRecord.id == RUN_ID)
            .values(version=2)
            .execution_options(synchronize_session=False)
        )
        run.version += 1
        with pytest.raises(RuntimeError, match="version changed"):
            await LifecycleWriter().append_run_lifecycle(
                database, run, "run.accepted", occurred_at=NOW, actor_type="system", actor_id=None
            )
        # Discard the rejected local mutation before committing the independent update.
        database.expunge(run)
        assert await database.scalar(select(func.count()).select_from(LifecycleEventRecord)) == 0
        assert await database.scalar(select(RunRecord.lifecycle_seq).where(RunRecord.id == RUN_ID)) == 0
