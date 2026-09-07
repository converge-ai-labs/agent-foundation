from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from a13n_harness import SafeFailure
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.interactions.lifecycle import RunEventType
from a13n_service.interactions.models import RunRecord
from a13n_service.lifecycle.domain import LifecycleProjectionState
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.lifecycle.retention import LifecycleRetentionReconciler
from a13n_service.storage import short_session, transaction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.hooks.support import RUN_ID, seed_run_and_secret
from tests.interactions.conftest import NOW
from tests.lifecycle_support import test_lifecycle_writer

pytestmark = pytest.mark.anyio

SECOND_RUN_ID = "run_2222222222222222"


async def _append_event(
    sessions: async_sessionmaker[AsyncSession],
    *,
    suffix: int,
    occurred_at: datetime,
    run_id: str = RUN_ID,
    event_type: RunEventType = "run.running",
) -> str:
    async with transaction(sessions) as database:
        run = await database.get(RunRecord, run_id)
        assert run is not None
        return await test_lifecycle_writer().append_run_lifecycle(
            database,
            run,
            event_type,
            mutation_id=f"mut_{suffix:016d}",
            occurred_at=occurred_at,
            actor_type="worker",
            actor_id="worker-1",
        )


async def _clone_run(sessions: async_sessionmaker[AsyncSession], *, run_id: str) -> None:
    async with transaction(sessions) as database:
        source = await database.get(RunRecord, RUN_ID)
        assert source is not None
        values = {column.name: getattr(source, column.name) for column in RunRecord.__table__.columns}
        values.update(
            id=run_id,
            status="failed",
            failure_json=SafeFailure(code="test_failure", message="Failed before execution.").model_dump(mode="json"),
            sealed_at=source.created_at,
        )
        database.add(RunRecord(**values))


async def _settle_events(
    sessions: async_sessionmaker[AsyncSession],
    *event_ids: str,
) -> None:
    async with transaction(sessions) as database:
        records = (
            await database.scalars(select(LifecycleEventRecord).where(LifecycleEventRecord.id.in_(event_ids)))
        ).all()
        assert len(records) == len(event_ids)
        for record in records:
            record.projection_state = LifecycleProjectionState.projected.value
            record.projection_next_attempt_at = None
            record.projected_at = record.created_at


def _delivery(
    *,
    suffix: int,
    source_id: str,
    status: str,
    timestamp: datetime,
) -> OutboxRecord:
    return OutboxRecord(
        id=f"out_{suffix:016d}",
        source_kind="lifecycle_event",
        source_id=source_id,
        destination_kind="webhook",
        destination_ref=f"hsubr_{suffix:016d}",
        status=status,
        available_at=timestamp,
        claim_generation=1,
        lease_expires_at=None,
        attempt_count=1,
        created_at=timestamp,
        updated_at=timestamp,
        published_at=timestamp if status == "published" else None,
        dead_lettered_at=timestamp if status == "dead_lettered" else None,
        last_error_code="delivery_failed" if status == "dead_lettered" else None,
    )


async def test_retention_releases_expired_terminal_deliveries_and_preserves_pins(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_run_and_secret(lifecycle_interaction_sessions)
    now = NOW + timedelta(days=100)
    unpinned_id = await _append_event(
        lifecycle_interaction_sessions,
        suffix=1,
        occurred_at=now - timedelta(days=20),
    )
    expired_published_id = await _append_event(
        lifecycle_interaction_sessions,
        suffix=2,
        occurred_at=now - timedelta(days=20),
    )
    pending_id = await _append_event(
        lifecycle_interaction_sessions,
        suffix=3,
        occurred_at=now - timedelta(days=20),
    )
    redriveable_id = await _append_event(
        lifecycle_interaction_sessions,
        suffix=4,
        occurred_at=now - timedelta(days=20),
    )
    unsettled_id = await _append_event(
        lifecycle_interaction_sessions,
        suffix=5,
        occurred_at=now - timedelta(days=20),
    )
    blocked_by_unsettled_prefix_id = await _append_event(
        lifecycle_interaction_sessions,
        suffix=6,
        occurred_at=now - timedelta(days=20),
    )
    fresh_id = await _append_event(
        lifecycle_interaction_sessions,
        suffix=7,
        occurred_at=now - timedelta(days=1),
    )
    await _settle_events(
        lifecycle_interaction_sessions,
        unpinned_id,
        expired_published_id,
        pending_id,
        redriveable_id,
        blocked_by_unsettled_prefix_id,
        fresh_id,
    )
    async with transaction(lifecycle_interaction_sessions) as database:
        database.add_all(
            (
                _delivery(
                    suffix=2,
                    source_id=expired_published_id,
                    status="published",
                    timestamp=now - timedelta(days=6),
                ),
                _delivery(
                    suffix=3,
                    source_id=pending_id,
                    status="pending",
                    timestamp=now - timedelta(days=20),
                ),
                _delivery(
                    suffix=4,
                    source_id=redriveable_id,
                    status="dead_lettered",
                    timestamp=now - timedelta(days=2),
                ),
            )
        )

    reconciler = LifecycleRetentionReconciler(
        lifecycle_interaction_sessions,
        event_horizon=timedelta(days=10),
        published_delivery_horizon=timedelta(days=5),
        dead_letter_horizon=timedelta(days=5),
        poll_interval_seconds=60,
        batch_limit=100,
        clock=lambda: now,
    )
    sweep = await reconciler.reconcile_once()

    assert sweep.outbox_records_deleted == 1
    assert sweep.lifecycle_events_deleted == 2
    async with short_session(lifecycle_interaction_sessions) as database:
        event_ids = set(await database.scalars(select(LifecycleEventRecord.id)))
        delivery_source_ids = set(await database.scalars(select(OutboxRecord.source_id)))
    assert event_ids == {
        pending_id,
        redriveable_id,
        unsettled_id,
        blocked_by_unsettled_prefix_id,
        fresh_id,
    }
    assert delivery_source_ids == {pending_id, redriveable_id}
    assert unpinned_id not in event_ids


async def test_retention_sweeps_events_in_bounded_batches(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_run_and_secret(lifecycle_interaction_sessions)
    now = NOW + timedelta(days=100)
    for suffix in range(10, 13):
        event_id = await _append_event(
            lifecycle_interaction_sessions, suffix=suffix, occurred_at=now - timedelta(days=20)
        )
        await _settle_events(lifecycle_interaction_sessions, event_id)
    reconciler = LifecycleRetentionReconciler(
        lifecycle_interaction_sessions,
        event_horizon=timedelta(days=10),
        published_delivery_horizon=timedelta(days=5),
        dead_letter_horizon=timedelta(days=5),
        poll_interval_seconds=60,
        batch_limit=2,
        clock=lambda: now,
    )

    first = await reconciler.reconcile_once()
    second = await reconciler.reconcile_once()

    assert first.lifecycle_events_deleted == 2
    assert second.lifecycle_events_deleted == 1
    async with short_session(lifecycle_interaction_sessions) as database:
        assert list(await database.scalars(select(LifecycleEventRecord.id))) == []


async def test_retention_preserves_organization_cursor_prefix_across_resources(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_run_and_secret(lifecycle_interaction_sessions)
    await _clone_run(lifecycle_interaction_sessions, run_id=SECOND_RUN_ID)
    now = NOW + timedelta(days=100)
    pinned_id = await _append_event(
        lifecycle_interaction_sessions,
        suffix=13,
        occurred_at=now - timedelta(days=20),
    )
    blocked_id = await _append_event(
        lifecycle_interaction_sessions,
        suffix=14,
        occurred_at=now - timedelta(days=20),
        run_id=SECOND_RUN_ID,
        event_type="run.failed",
    )
    await _settle_events(lifecycle_interaction_sessions, pinned_id, blocked_id)
    async with transaction(lifecycle_interaction_sessions) as database:
        database.add(
            _delivery(
                suffix=13,
                source_id=pinned_id,
                status="pending",
                timestamp=now - timedelta(days=20),
            )
        )
    reconciler = LifecycleRetentionReconciler(
        lifecycle_interaction_sessions,
        event_horizon=timedelta(days=10),
        published_delivery_horizon=timedelta(days=5),
        dead_letter_horizon=timedelta(days=5),
        poll_interval_seconds=60,
        batch_limit=100,
        clock=lambda: now,
    )

    sweep = await reconciler.reconcile_once()

    assert sweep.lifecycle_events_deleted == 0
    async with short_session(lifecycle_interaction_sessions) as database:
        event_ids = tuple(await database.scalars(select(LifecycleEventRecord.id).order_by(LifecycleEventRecord.seq)))
    assert event_ids == (pinned_id, blocked_id)


async def test_retention_prefix_query_runs_on_postgresql(
    lifecycle_postgres_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await seed_run_and_secret(lifecycle_postgres_sessions)
    now = NOW + timedelta(days=100)
    event_id = await _append_event(
        lifecycle_postgres_sessions,
        suffix=20,
        occurred_at=now - timedelta(days=20),
    )
    await _settle_events(lifecycle_postgres_sessions, event_id)
    reconciler = LifecycleRetentionReconciler(
        lifecycle_postgres_sessions,
        event_horizon=timedelta(days=10),
        published_delivery_horizon=timedelta(days=5),
        dead_letter_horizon=timedelta(days=5),
        poll_interval_seconds=60,
        batch_limit=100,
        clock=lambda: now,
    )

    assert (await reconciler.reconcile_once()).lifecycle_events_deleted == 1


async def test_retention_rejects_invalid_policy(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
) -> None:
    with pytest.raises(ValueError, match="horizons must be positive"):
        LifecycleRetentionReconciler(
            lifecycle_interaction_sessions,
            event_horizon=timedelta(0),
            published_delivery_horizon=timedelta(days=1),
            dead_letter_horizon=timedelta(days=1),
            poll_interval_seconds=60,
            batch_limit=100,
        )


async def test_asset_cleanup_retention_preserves_unfinished_progress(lifecycle_interaction_sessions):
    sessions = lifecycle_interaction_sessions
    now = NOW + timedelta(days=100)
    async with transaction(sessions) as database:
        for index, status in enumerate(("published", "dead_lettered", "pending"), start=70):
            record = _delivery(
                suffix=index, source_id=f"ast_{index}", status=status, timestamp=now - timedelta(days=40)
            )
            record.source_kind = "asset"
            record.destination_kind = "asset_content_cleanup"
            database.add(record)
    reconciler = LifecycleRetentionReconciler(
        sessions,
        event_horizon=timedelta(days=30),
        published_delivery_horizon=timedelta(days=7),
        dead_letter_horizon=timedelta(days=30),
        poll_interval_seconds=60,
        batch_limit=100,
        clock=lambda: now,
    )
    assert (await reconciler.reconcile_once()).outbox_records_deleted == 1
    async with short_session(sessions) as database:
        assert set(await database.scalars(select(OutboxRecord.status))) == {"dead_lettered", "pending"}
