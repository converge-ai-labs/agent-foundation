"""Single-statement Outbox transitions retain transactional and claim fences."""

from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.durable_operations.outbox import claim_outbox, complete_outbox, fail_outbox, redrive_outbox
from a13n_service.storage import short_session, transaction
from anyio import create_task_group, fail_after, sleep
from sqlalchemy import select, text
from tests.interactions.conftest import NOW
from tests.interactions.conftest import interaction_sessions as interaction_sessions
from tests.sql_capture import capture_sql

pytestmark = pytest.mark.anyio


def _record(identifier, **changes):
    fields = dict(
        id=identifier,
        source_kind="test",
        source_id=identifier,
        destination_kind="test",
        destination_ref="target",
        status="pending",
        available_at=NOW,
        claim_generation=0,
        attempt_count=0,
        created_at=NOW,
        updated_at=NOW,
    )
    return OutboxRecord(**(fields | changes))


async def _claim(database, *, now=NOW, limit=10):
    return await claim_outbox(
        database,
        source_kind="test",
        destination_kind="test",
        destination_ref="target",
        now=now,
        lease_duration=timedelta(seconds=30),
        limit=limit,
    )


@pytest.fixture
async def claimed(interaction_sessions):
    async with transaction(interaction_sessions) as database:
        database.add(_record("intent"))
    async with transaction(interaction_sessions) as database:
        return (await _claim(database))[0]


async def test_claim_is_one_statement_ordered_bounded_and_skips_locked_rows(interaction_sessions):
    sessions = interaction_sessions
    async with transaction(sessions) as database:
        database.add_all(
            [
                _record("first", available_at=NOW - timedelta(seconds=1)),
                _record("second"),
                _record("third"),
                _record("future", available_at=NOW + timedelta(seconds=1)),
                _record("other", destination_ref="other"),
            ]
        )
    async with transaction(sessions) as first_worker:
        with capture_sql(sessions) as statements:
            first = await _claim(first_worker, limit=1)
        assert len(statements) == 1 and "SKIP LOCKED" in statements[0]
        assert [item.outbox_id for item in first] == ["first"]
        with fail_after(2), capture_sql(sessions) as statements:
            async with transaction(sessions) as second_worker:
                second = await _claim(second_worker)
        assert len(statements) == 1
        assert [item.outbox_id for item in second] == ["second", "third"]
        assert all(item.generation == item.attempt_count == 1 for item in (*first, *second))
    with capture_sql(sessions) as statements:
        async with transaction(sessions) as database:
            assert await _claim(database) == ()
    assert len(statements) == 1


@pytest.mark.parametrize("transition", ["claim", "complete", "fail", "redrive"])
async def test_transition_rolls_back_with_owning_transaction(interaction_sessions, transition):
    sessions = interaction_sessions
    async with transaction(sessions) as database:
        database.add(_record("intent"))
    claim = None
    if transition != "claim":
        async with transaction(sessions) as database:
            claim = (await _claim(database))[0]
    if transition == "redrive":
        async with transaction(sessions) as database:
            await fail_outbox(
                database,
                claim,
                failed_at=NOW,
                error_code="failed",
                retryable=False,
                retry_after=timedelta(0),
                max_attempts=3,
            )
    async with short_session(sessions) as database:
        before = await database.get(OutboxRecord, "intent")
        expected = (before.status, before.claim_generation, before.attempt_count, before.lease_expires_at)
    with pytest.raises(RuntimeError, match="abort"):
        async with transaction(sessions) as database:
            if transition == "claim":
                assert await _claim(database)
            elif transition == "complete":
                assert await complete_outbox(database, claim, completed_at=NOW)
            elif transition == "fail":
                assert await fail_outbox(
                    database,
                    claim,
                    failed_at=NOW,
                    error_code="failed",
                    retryable=True,
                    retry_after=timedelta(seconds=2),
                    max_attempts=3,
                )
            else:
                assert await redrive_outbox(
                    database,
                    outbox_id="intent",
                    source_kind="test",
                    destination_kind="test",
                    destination_ref="target",
                    redriven_at=NOW,
                )
            raise RuntimeError("abort")
    async with short_session(sessions) as database:
        after = await database.get(OutboxRecord, "intent")
        assert (after.status, after.claim_generation, after.attempt_count, after.lease_expires_at) == expected


@pytest.mark.parametrize(
    "retryable,max_attempts,status", [(True, 2, "pending"), (True, 1, "dead_lettered"), (False, 2, "dead_lettered")]
)
async def test_failure_uses_durable_attempt_count_and_redrive_is_conditional(
    interaction_sessions, claimed, retryable, max_attempts, status
):
    sessions = interaction_sessions
    with capture_sql(sessions) as statements:
        async with transaction(sessions) as database:
            assert await fail_outbox(
                database,
                replace(claimed, attempt_count=99),
                failed_at=NOW,
                error_code="failed",
                retryable=retryable,
                retry_after=timedelta(seconds=2),
                max_attempts=max_attempts,
            )
    assert len(statements) == 1 and statements[0].startswith("UPDATE")
    async with short_session(sessions) as database:
        row = await database.get(OutboxRecord, "intent")
        assert row.status == status and row.lease_expires_at is None
        assert row.available_at == NOW + (timedelta(seconds=2) if status == "pending" else timedelta(0))
        assert row.dead_lettered_at == (NOW if status == "dead_lettered" else None)
        assert row.last_error_code == "failed"
    for destination, expected in [("wrong", False), ("target", status == "dead_lettered"), ("target", False)]:
        with capture_sql(sessions) as statements:
            async with transaction(sessions) as database:
                assert (
                    await redrive_outbox(
                        database,
                        outbox_id="intent",
                        source_kind="test",
                        destination_kind="test",
                        destination_ref=destination,
                        redriven_at=NOW,
                    )
                    is expected
                )
        assert len(statements) == 1
    if status == "dead_lettered":
        async with short_session(sessions) as database:
            row = await database.get(OutboxRecord, "intent")
            assert row.status == "pending" and row.attempt_count == 0
            assert row.claim_generation == claimed.generation and row.last_error_code is None


@pytest.mark.parametrize("operation", ["complete", "fail"])
@pytest.mark.parametrize(
    "mismatch",
    ["outbox_id", "source_kind", "source_id", "destination_kind", "destination_ref", "generation", "expired"],
)
async def test_settlement_preserves_every_claim_fence(interaction_sessions, claimed, operation, mismatch):
    claim = (
        claimed
        if mismatch == "expired"
        else replace(claimed, **{mismatch: 99 if mismatch == "generation" else "wrong"})
    )
    now = NOW + timedelta(seconds=30) if mismatch == "expired" else NOW
    with capture_sql(interaction_sessions) as statements:
        async with transaction(interaction_sessions) as database:
            if operation == "complete":
                result = await complete_outbox(database, claim, completed_at=now)
            else:
                result = await fail_outbox(
                    database,
                    claim,
                    failed_at=now,
                    error_code="failed",
                    retryable=False,
                    retry_after=timedelta(0),
                    max_attempts=1,
                )
            assert result is False
    assert len(statements) == 1
    async with short_session(interaction_sessions) as database:
        row = await database.get(OutboxRecord, "intent")
        assert row.status == "publishing" and row.claim_generation == claimed.generation


async def test_complete_is_single_statement_and_duplicate_does_not_mutate(interaction_sessions, claimed):
    for expected in (True, False):
        with capture_sql(interaction_sessions) as statements:
            async with transaction(interaction_sessions) as database:
                assert await complete_outbox(database, claimed, completed_at=NOW) is expected
        assert len(statements) == 1
    async with short_session(interaction_sessions) as database:
        row = await database.get(OutboxRecord, "intent")
        assert row.status == "published" and row.published_at == NOW
        assert row.lease_expires_at is None and row.last_error_code is None


async def test_waiting_settlement_rechecks_generation_after_concurrent_takeover(interaction_sessions, claimed):
    sessions = interaction_sessions

    async def settle():
        async with transaction(sessions) as database:
            assert not await complete_outbox(database, claimed, completed_at=NOW)

    with fail_after(5):
        async with create_task_group() as tasks:
            async with transaction(sessions) as blocker:
                row = await blocker.scalar(select(OutboxRecord).where(OutboxRecord.id == "intent").with_for_update())
                tasks.start_soon(settle)
                while True:
                    async with short_session(sessions) as observer:
                        blocked = await observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock' AND query LIKE 'UPDATE outbox_records%')"
                            )
                        )
                    if blocked:
                        break
                    await sleep(0.01)
                row.claim_generation += 1
    async with short_session(sessions) as database:
        row = await database.get(OutboxRecord, "intent")
        assert row.status == "publishing" and row.claim_generation == claimed.generation + 1
