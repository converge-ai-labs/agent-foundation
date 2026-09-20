from datetime import timedelta
from unittest.mock import AsyncMock

import anyio
import pytest
from a13n_harness import SafeFailure
from a13n_service.interactions.models import RunRecord
from a13n_service.lifecycle.models import LifecycleEventRecord
from a13n_service.run_stream import RedisRunStream, RunDisplayStore
from a13n_service.run_stream.display_candidates import DisplayCandidate, DisplayCandidates
from a13n_service.run_stream.display_consumer import RunDisplayConsumer
from a13n_service.run_stream.domain import PublicationUnavailable
from a13n_service.run_stream.projector import LifecycleRunStreamProjector
from a13n_service.run_stream.redis import _keys
from a13n_service.storage import ObjectStoreUnavailable, transaction
from a13n_service.temporal import utc_now
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError
from tests.hooks.support import RUN_ID, seed_run_and_secret
from tests.interactions.conftest import ORGANIZATION_ID, THREAD_ID
from tests.lifecycle_support import test_lifecycle_writer
from tests.run_stream.support import activate_stream

pytestmark = pytest.mark.anyio


async def _seal(sessions, sealed_at):
    await seed_run_and_secret(sessions)
    async with transaction(sessions) as database:
        run = await database.get(RunRecord, RUN_ID)
        run.status = "failed"
        run.sealed_at = sealed_at
        run.failure_json = SafeFailure(code="test_failure", message="Failed.").model_dump(mode="json")
        for number, kind in enumerate(("run.accepted", "run.failed")):
            await test_lifecycle_writer().append_run_lifecycle(
                database,
                run,
                kind,
                mutation_id=f"mut_{number:016d}",
                occurred_at=sealed_at,
                actor_type="worker",
                actor_id="worker-test",
            )
        await database.execute(
            update(LifecycleEventRecord)
            .where(LifecycleEventRecord.run_id == RUN_ID)
            .values(
                projection_state="projected",
                projection_next_attempt_at=None,
                projected_at=sealed_at,
            )
        )
    return DisplayCandidate(ORGANIZATION_ID, RUN_ID, THREAD_ID, sealed_at)


async def test_final_put_before_ack_crash_recovers_and_leaves_discovery(
    lifecycle_interaction_sessions,
    redis_client,
    object_store,
    monkeypatch,
):
    sessions = lifecycle_interaction_sessions
    candidate = await _seal(sessions, utc_now())
    stream = RedisRunStream(redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=candidate.sealed_at)
    store = RunDisplayStore(object_store)
    candidates = DisplayCandidates(sessions)
    ack = stream.acknowledge_display
    monkeypatch.setattr(stream, "acknowledge_display", AsyncMock(side_effect=PublicationUnavailable("lost ACK")))
    with pytest.raises(PublicationUnavailable):
        await RunDisplayConsumer(candidates, stream, store).consume_run(candidate)
    persisted = await store.read(ORGANIZATION_ID, RUN_ID, expected_thread_id=THREAD_ID)
    assert persisted.snapshot.finalized and persisted.snapshot.complete
    assert await candidates.page(lane="recovery", after=None, limit=10, now=utc_now()) == (candidate,)
    monkeypatch.setattr(stream, "acknowledge_display", ack)
    assert await RunDisplayConsumer(DisplayCandidates(sessions), stream, store).consume_run(candidate) == persisted
    # A newly constructed consumer cannot rediscover successfully settled history.
    assert await DisplayCandidates(sessions).page(lane="recovery", after=None, limit=10, now=utc_now()) == ()


async def test_expired_cleanup_survives_missing_ttl_and_never_touches_object_storage(
    lifecycle_interaction_sessions,
    redis_client,
):
    sessions = lifecycle_interaction_sessions
    await _seal(sessions, utc_now() - timedelta(days=2))
    stream = RedisRunStream(redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    assert all([await redis_client.ttl(key) == -1 for key in _keys(ORGANIZATION_ID, RUN_ID)])
    store = AsyncMock(spec=RunDisplayStore)
    store.read.side_effect = ObjectStoreUnavailable("outage beyond recovery window")
    consumer = RunDisplayConsumer(DisplayCandidates(sessions), stream, store)
    assert await consumer.consume_once(lane="recovery") == 0
    assert await consumer.consume_once(lane="cleanup") == 1
    store.read.assert_not_awaited()
    store.publish.assert_not_awaited()
    assert all([not await redis_client.exists(key) for key in _keys(ORGANIZATION_ID, RUN_ID)])
    assert await RunDisplayConsumer(DisplayCandidates(sessions), stream, store).consume_once(lane="cleanup") == 0


async def test_storage_failure_installs_expiry_and_persists_retry_delay(
    lifecycle_interaction_sessions,
    redis_client,
):
    sessions = lifecycle_interaction_sessions
    now = utc_now()
    candidate = await _seal(sessions, now)
    stream = RedisRunStream(redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    store = AsyncMock(spec=RunDisplayStore)
    store.read.side_effect = ObjectStoreUnavailable("offline")
    consumer = RunDisplayConsumer(DisplayCandidates(sessions), stream, store, clock=lambda: now)
    assert await consumer.consume_once(lane="recovery") == 1
    assert all([0 < await redis_client.ttl(key) <= 86400 for key in _keys(ORGANIZATION_ID, RUN_ID)])
    restarted = DisplayCandidates(sessions)
    assert await restarted.page(lane="recovery", after=None, limit=10, now=now) == ()
    assert await restarted.page(lane="recovery", after=None, limit=10, now=now + timedelta(seconds=5)) == (candidate,)


async def test_delayed_lifecycle_projection_does_not_recreate_expired_history(
    lifecycle_interaction_sessions,
    redis_client,
    monkeypatch,
):
    sessions = lifecycle_interaction_sessions
    now = utc_now()
    await _seal(sessions, now - timedelta(days=2))
    async with transaction(sessions) as database:
        await database.execute(
            update(LifecycleEventRecord)
            .where(LifecycleEventRecord.run_id == RUN_ID)
            .values(
                projection_state="pending",
                projection_next_attempt_at=now,
                projected_at=None,
            )
        )
    stream = RedisRunStream(redis_client)
    initialize = AsyncMock(side_effect=AssertionError("Expired history must not reopen"))
    monkeypatch.setattr(stream, "initialize", initialize)
    projector = LifecycleRunStreamProjector(sessions, stream, worker_id="late-projector", clock=lambda: now)
    assert await projector.project_once() == 1
    assert await projector.project_once() == 1
    assert await projector.project_once() == 0
    initialize.assert_not_awaited()
    assert all([not await redis_client.exists(key) for key in _keys(ORGANIZATION_ID, RUN_ID)])


async def test_scheduling_metadata_does_not_change_or_unseal_execution(lifecycle_interaction_sessions):
    sessions = lifecycle_interaction_sessions
    candidate = await _seal(sessions, utc_now())
    async with transaction(sessions) as database:
        before = (await database.get(RunRecord, RUN_ID)).to_resource()
    await DisplayCandidates(sessions).settle(candidate, now=utc_now())
    async with transaction(sessions) as database:
        row = await database.get(RunRecord, RUN_ID)
        assert row.display_settled_at is not None
        assert row.to_resource() == before
    # Neither relabeling nor display bookkeeping may move the immutable anchor
    # and turn an expired recovery window into a fresh one.
    with pytest.raises(DBAPIError, match="sealed Run rows are immutable"):
        async with transaction(sessions) as database:
            await database.execute(update(RunRecord).where(RunRecord.id == RUN_ID).values(sealed_at=utc_now()))


async def test_slow_recovery_does_not_block_active_consumption():
    candidates = AsyncMock(spec=DisplayCandidates)
    active = DisplayCandidate("org_test", "run_active", "thread_active")
    recovering = DisplayCandidate("org_test", "run_recovery", "thread_recovery", utc_now())
    entered = anyio.Event()
    progressed = anyio.Event()
    release = anyio.Event()

    async def page(*, lane, **kwargs):
        return (active,) if lane == "active" else (recovering,) if lane == "recovery" else ()

    candidates.page.side_effect = page
    consumer = RunDisplayConsumer(candidates, AsyncMock(spec=RedisRunStream), AsyncMock(spec=RunDisplayStore))

    async def consume(candidate):
        if candidate == recovering:
            entered.set()
            await release.wait()
        else:
            await entered.wait()
            progressed.set()
            consumer._draining = True
            release.set()

    consumer.consume_run = consume
    with anyio.fail_after(3):
        await consumer.run()
    assert progressed.is_set()
