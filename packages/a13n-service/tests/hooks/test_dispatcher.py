"""Real PostgreSQL dispatch boundaries, current matching, and recovery."""

from datetime import timedelta

import anyio
import pytest
from a13n_service.durable_operations.models import OutboxRecord
from a13n_service.hooks.dispatch import claim_hook_events, dispatch_hook_event, retry_failed_hook_dispatch
from a13n_service.hooks.dispatcher import HookDispatcher
from a13n_service.hooks.models import HookSubscriptionRecord, HookSubscriptionRevisionRecord
from a13n_service.hooks.persistence import create_hook_subscription
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.interactions.models import RunRecord
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from sqlalchemy import select, text

from tests.hooks.support import RUN_ID, seed_run_and_secret
from tests.hooks.test_persistence import _input
from tests.interactions.conftest import NOW, ORGANIZATION_ID, SESSION_ID, THREAD_ID, USER_ID, WORKSPACE_ID

pytestmark = pytest.mark.anyio


async def _emit(sessions, count=1):
    ids = []
    async with transaction(sessions) as database:
        run = await database.get(RunRecord, RUN_ID, with_for_update=True)
        for _ in range(count):
            run.version += 1
            ids.append(
                await LifecycleWriter().append_run_lifecycle(
                    database, run, "run.accepted", occurred_at=NOW, actor_type="system", actor_id=None
                )
            )
    return ids


async def _subscribe(sessions):
    async with transaction(sessions) as database:
        return await create_hook_subscription(
            database,
            organization_id=ORGANIZATION_ID,
            workspace_id=WORKSPACE_ID,
            actor_type="user",
            actor_id=USER_ID,
            subscription=_input("run.accepted").bind_run_scope(
                session_id=SESSION_ID, thread_id=THREAD_ID, run_id=RUN_ID
            ),
            now=NOW,
        )


async def test_source_commit_is_independent_and_zero_matches_complete_once(hook_interaction_sessions):
    sessions = hook_interaction_sessions
    await seed_run_and_secret(sessions)
    (event_id,) = await _emit(sessions)
    async with short_session(sessions) as database:
        event = await database.scalar(select(LifecycleEventRecord))
        assert event.hook_dispatch_state == event.projection_state == "pending"
        assert event.hook_dispatch_attempts == 0
        assert await database.scalar(select(OutboxRecord.id)) is None

    dispatcher = HookDispatcher(sessions, clock=lambda: NOW)
    assert (await dispatcher.scan()).completed == 1
    await _subscribe(sessions)
    assert (await dispatcher.scan()).examined == 0
    async with short_session(sessions) as database:
        event = await database.scalar(select(LifecycleEventRecord).where(LifecycleEventRecord.id == event_id))
        assert event.hook_dispatch_state == "done" and event.hook_dispatch_attempts == 1
        assert event.hook_dispatched_at == NOW and event.hook_dispatch_next_attempt_at is None
        assert event.projection_state == "pending" and event.projection_attempts == 0
        assert await database.scalar(select(OutboxRecord.id)) is None


@pytest.mark.parametrize("change", ["created", "paused", "deleted", "revision"])
async def test_matching_uses_subscription_state_after_source_commit(hook_interaction_sessions, change):
    sessions = hook_interaction_sessions
    await seed_run_and_secret(sessions)
    head = None if change == "created" else await _subscribe(sessions)
    (event_id,) = await _emit(sessions)
    if change == "created":
        head = await _subscribe(sessions)
    else:
        async with transaction(sessions) as database:
            current = await database.get(HookSubscriptionRecord, head.id, with_for_update=True)
            if change == "paused":
                current.enabled = False
            elif change == "deleted":
                current.deleted_at = NOW
            else:
                old = await database.get(HookSubscriptionRevisionRecord, current.current_revision_id)
                values = {column.name: getattr(old, column.name) for column in old.__table__.columns}
                revision_id = "hsubr_9292929292929292"
                database.add(
                    HookSubscriptionRevisionRecord(
                        **(values | {"id": revision_id, "version": 2, "endpoint_url": "https://new.example.com/hook"})
                    )
                )
                current.current_revision_id = revision_id
                current.version = 2
    assert (await HookDispatcher(sessions, clock=lambda: NOW).scan()).completed == 1
    async with short_session(sessions) as database:
        deliveries = tuple(await database.scalars(select(OutboxRecord)))
        if change in {"paused", "deleted"}:
            assert deliveries == ()
        else:
            assert len(deliveries) == 1
            assert deliveries[0].source_id == event_id
            assert deliveries[0].destination_ref == (
                "hsubr_9292929292929292" if change == "revision" else head.current_revision_id
            )


async def test_overlapping_workers_skip_claims_and_rollback_leaves_no_cursor_hole(hook_interaction_sessions):
    sessions = hook_interaction_sessions
    await seed_run_and_secret(sessions)
    await _subscribe(sessions)
    event_ids = await _emit(sessions, 4)
    dispatcher = HookDispatcher(sessions, batch_limit=2, clock=lambda: NOW)

    with pytest.raises(RuntimeError, match="worker lost"), anyio.fail_after(10):
        async with transaction(sessions) as first:
            claims = await claim_hook_events(first, now=NOW, limit=2)
            assert [event.id for event in claims] == event_ids[:2]
            assert (await dispatcher.scan()).completed == 2
            async with short_session(sessions) as observer:
                assert set(await observer.scalars(select(OutboxRecord.source_id))) == set(event_ids[2:])
            for event in claims:
                await dispatch_hook_event(first, event, now=NOW)
            raise RuntimeError("worker lost before commit")

    assert (await dispatcher.scan()).completed == 2
    assert (await dispatcher.scan()).examined == 0
    async with short_session(sessions) as database:
        assert set(await database.scalars(select(OutboxRecord.source_id))) == set(event_ids)
        assert len(tuple(await database.scalars(select(OutboxRecord.id)))) == 4
        assert set(await database.scalars(select(LifecycleEventRecord.hook_dispatch_state))) == {"done"}


async def test_failed_fanout_is_atomic_isolated_backed_off_and_explicitly_retryable(
    hook_interaction_sessions, monkeypatch, caplog
):
    sessions = hook_interaction_sessions
    await seed_run_and_secret(sessions)
    await _subscribe(sessions)
    event_ids = await _emit(sessions, 2)
    now = NOW
    dispatcher = HookDispatcher(sessions, max_attempts=2, clock=lambda: now)

    async def fail_after_writes(database, event, *, now):
        result = await dispatch_hook_event(database, event, now=now)
        if event.id == event_ids[0]:
            # A real SQL error aborts the savepoint after all destination writes.
            await database.execute(text("SELECT 1 / 0"))
        return result

    with monkeypatch.context() as patch:
        patch.setattr("a13n_service.hooks.dispatcher.dispatch_hook_event", fail_after_writes)
        result = await dispatcher.scan()
        assert (result.examined, result.completed, result.deferred, result.failed) == (2, 1, 1, 0)
        assert (await dispatcher.scan()).examined == 0
        async with short_session(sessions) as database:
            event = await database.scalar(select(LifecycleEventRecord).where(LifecycleEventRecord.id == event_ids[0]))
            assert event.hook_dispatch_state == "pending" and event.hook_dispatch_attempts == 1
            assert event.hook_dispatch_next_attempt_at == NOW + timedelta(seconds=2)
            assert event.hook_dispatched_at is None
            assert event.hook_dispatch_error_json["code"] == "hook_dispatch_failed"
            assert tuple(await database.scalars(select(OutboxRecord.source_id))) == (event_ids[1],)
            assert (await database.get(RunRecord, RUN_ID)).version == 3
        now += timedelta(seconds=2)
        assert (await dispatcher.scan()).failed == 1
        assert (await dispatcher.scan()).examined == 0

    assert "hook_dispatch_failed" in caplog.text
    async with transaction(sessions) as database:
        assert not await retry_failed_hook_dispatch(
            database, organization_id="org_other", event_id=event_ids[0], now=now
        )
        assert not await retry_failed_hook_dispatch(
            database, organization_id=ORGANIZATION_ID, event_id=event_ids[1], now=now
        )
        assert await retry_failed_hook_dispatch(
            database, organization_id=ORGANIZATION_ID, event_id=event_ids[0], now=now
        )
    assert (await dispatcher.scan()).completed == 1
    async with short_session(sessions) as database:
        events = tuple(await database.scalars(select(LifecycleEventRecord)))
        assert all(event.hook_dispatch_state == "done" and event.hook_dispatch_error_json is None for event in events)
        assert set(await database.scalars(select(OutboxRecord.source_id))) == set(event_ids)


async def test_cancellation_rolls_back_fanout_and_releases_claim(hook_interaction_sessions, monkeypatch):
    sessions = hook_interaction_sessions
    await seed_run_and_secret(sessions)
    await _subscribe(sessions)
    await _emit(sessions)
    written = anyio.Event()

    async def pause_before_commit(database, event, *, now):
        await dispatch_hook_event(database, event, now=now)
        written.set()
        await anyio.sleep_forever()

    dispatcher = HookDispatcher(sessions, clock=lambda: NOW)
    with monkeypatch.context() as patch, anyio.fail_after(10):
        patch.setattr("a13n_service.hooks.dispatcher.dispatch_hook_event", pause_before_commit)
        async with anyio.create_task_group() as group:
            group.start_soon(dispatcher.scan)
            await written.wait()
            group.cancel_scope.cancel()
    async with short_session(sessions) as database:
        assert await database.scalar(select(OutboxRecord.id)) is None
        assert await database.scalar(select(LifecycleEventRecord.hook_dispatch_state)) == "pending"
    assert (await dispatcher.scan()).completed == 1


async def test_revision_collection_contention_defers_without_waiting(hook_interaction_sessions):
    sessions = hook_interaction_sessions
    await seed_run_and_secret(sessions)
    head = await _subscribe(sessions)
    await _emit(sessions)
    now = NOW
    dispatcher = HookDispatcher(sessions, clock=lambda: now)
    with anyio.fail_after(5):
        async with transaction(sessions) as collector:
            await collector.get(HookSubscriptionRevisionRecord, head.current_revision_id, with_for_update=True)
            assert (await dispatcher.scan()).deferred == 1
    now += timedelta(seconds=2)
    assert (await dispatcher.scan()).completed == 1


def test_dispatch_settings_validate_backoff_independently_of_http_delivery():
    with pytest.raises(ValueError, match="Hook dispatch maximum retry delay"):
        Settings(hooks={"dispatch_retry_base_seconds": 10, "dispatch_retry_max_seconds": 9})
    settings = Settings(hooks={"dispatch_batch_limit": 4, "dispatch_max_attempts": 3})
    assert settings.hooks.dispatch_batch_limit == 4 and settings.webhooks.claim_limit == 25
