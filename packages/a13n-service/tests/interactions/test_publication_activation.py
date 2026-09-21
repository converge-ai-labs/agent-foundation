from __future__ import annotations

import json
from datetime import timedelta

import pytest
from a13n_service.interactions.attempts import AttemptAuthorityError
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt, SealedClaim
from a13n_service.lifecycle import LifecycleEventRecord
from a13n_service.run_stream import (
    LifecycleRunStreamProjector,
    RedisRunStream,
    RetainedReplayUnavailable,
    RunDisplayStore,
    RunStreamEvent,
    deterministic_run_stream_event_id,
)
from a13n_service.run_stream.activation import PublicationActivator
from a13n_service.run_stream.display_candidates import DisplayCandidate, DisplayCandidates
from a13n_service.run_stream.display_consumer import RunDisplayConsumer
from a13n_service.run_stream.domain import PublicationRejected, PublicationUnavailable
from a13n_service.run_stream.events import lifecycle_stream_event
from a13n_service.run_stream.redis import _RETIREMENT_SCRIPT, _keys
from a13n_service.storage import short_session
from a13n_service.temporal import utc_now
from redis.exceptions import ConnectionError
from sqlalchemy import select

from tests.lifecycle_support import test_lifecycle_writer
from tests.run_stream.support import publication_failure

from .conftest import NOW, ORGANIZATION_ID
from .test_attempt_execution import _accept_root, _authority, _worker

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("failure_point", ["event", "receipts", "retention", "lost_ack"])
async def test_worker_retries_the_partial_activation_without_reinitializing(
    interaction_sessions, interaction_object_store, redis_client, failure_point
):
    sessions = interaction_sessions
    _, run, _ = await _accept_root(sessions, interaction_object_store)
    now = NOW + timedelta(seconds=1)
    claim = await AttemptScheduler(sessions, clock=lambda: now, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    stream = RedisRunStream(redis_client)
    healthy = stream._script
    faulty = (
        healthy
        if failure_point == "lost_ack"
        else redis_client.register_script(publication_failure("activate", after=failure_point))
    )
    calls = []

    async def fail_once(**kwargs):
        operation = json.loads(kwargs["args"][0])["operation"]
        calls.append(operation)
        if operation == "activate":
            stream._script = healthy
            result = await faulty(**kwargs)
            if failure_point == "lost_ack":
                raise ConnectionError("response lost after activation")
            return result
        return await healthy(**kwargs)

    stream._script = fail_once
    await PublicationActivator(sessions, stream, clock=lambda: now).activate(_authority(claim))

    assert calls == ["initialize", "activate"]
    page = await stream.read(ORGANIZATION_ID, run.id, after_stream_id=None, limit=10)
    assert [entry.event.event_type for entry in page.items] == ["run.accepted", "run_attempt.leased"]
    await stream.append(
        ORGANIZATION_ID,
        RunStreamEvent(
            event_id=deterministic_run_stream_event_id("retry-test", "observation"),
            event_type="agui.custom",
            run_id=run.id,
            thread_id=run.thread_id,
            run_attempt_id=claim.attempt.id,
            occurred_at=now,
            payload={},
        ),
        attempt_number=claim.attempt.attempt_number,
    )


@pytest.mark.parametrize("successor", [False, True])
async def test_lifecycle_projector_recovers_activation_after_worker_disappears(
    interaction_sessions, interaction_object_store, redis_client, successor
):
    sessions = interaction_sessions
    _, run, _ = await _accept_root(sessions, interaction_object_store)
    now = NOW + timedelta(seconds=1)
    claim = await AttemptScheduler(sessions, clock=lambda: now, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker(lease_seconds=1 if successor else 60)
    )
    assert isinstance(claim, ClaimedAttempt)
    stream = RedisRunStream(redis_client)
    activation = PublicationActivator(sessions, stream, clock=lambda: now)
    if successor:
        await activation.activate(_authority(claim))
        now += timedelta(seconds=2)
        claim = await AttemptScheduler(sessions, clock=lambda: now, lifecycle=test_lifecycle_writer()).claim(
            run.id, _worker(worker_id="worker-2")
        )
        assert isinstance(claim, ClaimedAttempt)
    healthy = stream._script
    stream._script = redis_client.register_script(publication_failure("activate", after="event"))
    with pytest.raises(PublicationUnavailable):
        await activation.activate(_authority(claim))
    stream._script = healthy
    projector = LifecycleRunStreamProjector(
        sessions,
        stream,
        worker_id="repair-worker",
        clock=lambda: now,
    )
    for _ in range(5):
        if not await projector.project_once():
            break

    page = await stream.read(ORGANIZATION_ID, run.id, after_stream_id=None, limit=100)
    openings = [entry.event for entry in page.items if entry.event.event_type == "run_attempt.leased"]
    assert [event.run_attempt_id for event in openings][-1] == claim.attempt.id
    assert len(openings) == (2 if successor else 1)
    assert sum(entry.event.event_type == "run.recovery" for entry in page.items) == int(successor)
    async with short_session(sessions) as database:
        assert set(await database.scalars(select(LifecycleEventRecord.projection_state))) == {"projected"}


@pytest.mark.parametrize("lost", ["incarnation", "events", "metadata", "both", "partial_activation"])
async def test_terminal_projection_retires_unavailable_history_without_reactivating_the_old_attempt(
    interaction_sessions, interaction_object_store, redis_client, lost
):
    sessions = interaction_sessions
    _, run, _ = await _accept_root(sessions, interaction_object_store, max_attempts=1)
    now = utc_now()
    scheduler = AttemptScheduler(sessions, clock=lambda: now, lifecycle=test_lifecycle_writer())
    claim = await scheduler.claim(run.id, _worker(lease_seconds=1))
    assert isinstance(claim, ClaimedAttempt)
    stream = RedisRunStream(redis_client, closed_ttl_seconds=60)
    activation = PublicationActivator(sessions, stream, clock=lambda: now)
    events, metadata = _keys(ORGANIZATION_ID, run.id)
    if lost == "partial_activation":
        healthy = stream._script
        stream._script = redis_client.register_script(publication_failure("activate", after="event"))
        with pytest.raises(PublicationUnavailable):
            await activation.activate(_authority(claim))
        stream._script = healthy
    else:
        await activation.activate(_authority(claim))
        if lost == "incarnation":
            await redis_client.hset(metadata, "server_id", "previous-primary")
        else:
            await redis_client.delete(*{"events": [events], "metadata": [metadata], "both": [events, metadata]}[lost])
    retained_rows = await redis_client.xrange(events)
    now += timedelta(seconds=2)
    assert isinstance(await scheduler.claim(run.id, _worker(worker_id="worker-2")), SealedClaim)
    projector = LifecycleRunStreamProjector(
        sessions,
        stream,
        worker_id="retirement-worker",
        max_attempts=1,
        clock=lambda: now,
    )
    for _ in range(10):
        if not await projector.project_once():
            break

    async with short_session(sessions) as database:
        run_record = await database.get(RunRecord, run.id)
        assert run_record.status == "failed"
        terminal = await database.scalar(
            select(LifecycleEventRecord).where(
                LifecycleEventRecord.run_id == run.id, LifecycleEventRecord.event_type == "run.failed"
            )
        )
        assert terminal.projection_state == "abandoned"
    assert await redis_client.xrange(events) == retained_rows
    for key in (events, metadata):
        assert await redis_client.ttl(key) == -2 or await redis_client.expiretime(key) == int(
            (now + timedelta(days=1)).timestamp()
        )
    stored = await RunDisplayConsumer(
        DisplayCandidates(sessions), stream, RunDisplayStore(interaction_object_store)
    ).consume_run(DisplayCandidate(ORGANIZATION_ID, run.id, run.thread_id))
    assert stored is not None and stored.snapshot.finalized and not stored.snapshot.complete
    for key in (events, metadata):
        ttl = await redis_client.ttl(key)
        assert ttl == -2 or 0 < ttl <= 60
    assert await redis_client.hget(metadata, "pending") is None
    with pytest.raises(AttemptAuthorityError):
        await activation.activate(_authority(claim))
    with pytest.raises(RetainedReplayUnavailable):
        await stream.complete_source(ORGANIZATION_ID, run.id)


@pytest.mark.parametrize("failure", ["partial_close", "lost_ack"])
async def test_terminal_cleanup_keeps_retrying_after_publication_budget_is_exhausted(
    interaction_sessions, interaction_object_store, redis_client, failure
):
    sessions = interaction_sessions
    _, run, _ = await _accept_root(sessions, interaction_object_store, max_attempts=1)
    now = utc_now()
    scheduler = AttemptScheduler(sessions, clock=lambda: now, lifecycle=test_lifecycle_writer())
    claim = await scheduler.claim(run.id, _worker(lease_seconds=1))
    assert isinstance(claim, ClaimedAttempt)
    stream = RedisRunStream(redis_client, closed_ttl_seconds=60)
    await PublicationActivator(sessions, stream, clock=lambda: now).activate(_authority(claim))
    events, metadata = _keys(ORGANIZATION_ID, run.id)
    await redis_client.hset(metadata, "server_id", "previous-primary")
    now += timedelta(seconds=2)
    assert isinstance(await scheduler.claim(run.id, _worker(worker_id="worker-2")), SealedClaim)
    projector = LifecycleRunStreamProjector(
        sessions,
        stream,
        worker_id="retirement-worker",
        retry_after=timedelta(0),
        max_attempts=1,
        clock=lambda: now,
    )
    healthy = stream._retirement_script
    if failure == "partial_close":
        marker = "redis.call('HDEL', metadata, 'pending')"
        assert _RETIREMENT_SCRIPT.count(marker) == 1
        faulty = redis_client.register_script(
            _RETIREMENT_SCRIPT.replace(marker, "error('retirement failed')\n" + marker)
        )
    else:
        faulty = healthy

    async def interrupt_retirement(**kwargs):
        if json.loads(kwargs["args"][0])["operation"] == "schedule_expiry":
            return await healthy(**kwargs)
        stream._retirement_script = healthy
        result = await faulty(**kwargs)
        if failure == "lost_ack":
            raise ConnectionError("response lost after retirement")
        return result

    stream._retirement_script = interrupt_retirement
    for _ in range(10):
        assert await projector.project_once() == 1
        if await redis_client.hget(metadata, "closed_at"):
            break
    assert await redis_client.hget(metadata, "retention_deadline") is None
    async with short_session(sessions) as database:
        terminal = await database.scalar(
            select(LifecycleEventRecord).where(
                LifecycleEventRecord.run_id == run.id, LifecycleEventRecord.event_type == "run.failed"
            )
        )
        assert (terminal.projection_state, terminal.projection_attempts) == ("retry_wait", 1)

    async def no_more_publication(**kwargs):
        pytest.fail("Retirement retries must not reenter exhausted publication")

    publication_script = stream._script
    stream._script = no_more_publication
    assert await projector.project_once() == 1
    assert await projector.project_once() == 0
    stream._script = publication_script
    assert await redis_client.expiretime(events) == int((now + timedelta(days=1)).timestamp())
    assert await redis_client.expiretime(metadata) == int((now + timedelta(days=1)).timestamp())
    stored = await RunDisplayConsumer(
        DisplayCandidates(sessions), stream, RunDisplayStore(interaction_object_store)
    ).consume_run(DisplayCandidate(ORGANIZATION_ID, run.id, run.thread_id))
    assert stored is not None and stored.snapshot.finalized and not stored.snapshot.complete
    deadline = await redis_client.hget(metadata, "retention_deadline")
    assert deadline is not None
    assert await redis_client.expiretime(events) == int(deadline)
    assert await redis_client.expiretime(metadata) == int(deadline)
    async with short_session(sessions) as database:
        terminal = await database.get(LifecycleEventRecord, terminal.seq)
        assert (terminal.projection_state, terminal.projection_attempts) == ("abandoned", 2)


@pytest.mark.parametrize("first_activated", [False, True])
async def test_durable_claim_and_activation_are_separate_ordered_boundaries(
    interaction_sessions, interaction_object_store, redis_client, first_activated
):
    sessions = interaction_sessions
    _, run, _ = await _accept_root(sessions, interaction_object_store)
    first = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        lifecycle=test_lifecycle_writer(),
    ).claim(run.id, _worker(lease_seconds=1))
    assert isinstance(first, ClaimedAttempt)
    stream = RedisRunStream(redis_client)
    activation = PublicationActivator(sessions, stream, clock=lambda: NOW + timedelta(seconds=1))
    async with short_session(sessions) as database:
        old_fact = (
            await database.scalar(
                select(LifecycleEventRecord).where(
                    LifecycleEventRecord.run_attempt_id == first.attempt.id,
                    LifecycleEventRecord.event_type == "run_attempt.leased",
                )
            )
        ).to_resource()
    if first_activated:
        await activation.activate(_authority(first))
    second = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=3),
        lifecycle=test_lifecycle_writer(),
    ).claim(run.id, _worker(worker_id="worker-2"))
    assert isinstance(second, ClaimedAttempt)
    observation = RunStreamEvent(
        event_id=deterministic_run_stream_event_id("activation-test", "old"),
        event_type="agui.custom",
        run_id=run.id,
        thread_id=run.thread_id,
        run_attempt_id=first.attempt.id,
        occurred_at=NOW,
        payload={"value": "accepted before presentation switch"},
    )
    if first_activated:
        await stream.append(ORGANIZATION_ID, observation, attempt_number=1)
    activation = PublicationActivator(sessions, stream, clock=lambda: NOW + timedelta(seconds=3))
    await activation.activate(_authority(second))
    await activation.activate(_authority(second))
    await activation.project_leased(old_fact)
    with pytest.raises(AttemptAuthorityError):
        await activation.activate(_authority(first))
    with pytest.raises(PublicationRejected):
        await stream.append(ORGANIZATION_ID, observation, attempt_number=1)
    page = await stream.read(ORGANIZATION_ID, run.id, after_stream_id=None, limit=100)
    openings = [entry.event for entry in page.items if entry.event.event_type == "run_attempt.leased"]
    assert [event.run_attempt_id for event in openings] == (
        [first.attempt.id, second.attempt.id] if first_activated else [second.attempt.id]
    )
    assert page.items[-2].event.run_attempt_id == second.attempt.id
    assert page.items[-1].event.event_type == "run.recovery"
    assert page.items[-1].event.payload == {"reason": "lease_expired"}
    async with short_session(sessions) as database:
        facts = tuple(
            await database.scalars(
                select(LifecycleEventRecord).where(
                    LifecycleEventRecord.event_type.in_(("run.accepted", "run_attempt.leased"))
                )
            )
        )
        assert all(fact.projection_state == "projected" for fact in facts)
        delayed_failure = await database.scalar(
            select(LifecycleEventRecord).where(
                LifecycleEventRecord.run_attempt_id == first.attempt.id,
                LifecycleEventRecord.event_type == "run_attempt.failed",
            )
        )
    assert delayed_failure is not None
    await stream.append_lifecycle(ORGANIZATION_ID, lifecycle_stream_event(delayed_failure.to_resource()))
    fresh = observation.model_copy(
        update={
            "event_id": deterministic_run_stream_event_id("activation-test", "new"),
            "run_attempt_id": second.attempt.id,
        }
    )
    await stream.append(ORGANIZATION_ID, fresh, attempt_number=2)


async def test_projected_bootstrap_cannot_recreate_lost_publication_state(
    interaction_sessions, interaction_object_store, redis_client
):
    sessions = interaction_sessions
    _, run, _ = await _accept_root(sessions, interaction_object_store)
    claim = await AttemptScheduler(
        sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        lifecycle=test_lifecycle_writer(),
    ).claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    stream = RedisRunStream(redis_client)
    activation = PublicationActivator(sessions, stream, clock=lambda: NOW + timedelta(seconds=1))
    async with short_session(sessions) as database:
        stale_accepted = (
            await database.scalar(select(LifecycleEventRecord).where(LifecycleEventRecord.event_type == "run.accepted"))
        ).to_resource()
    await activation.activate(_authority(claim))
    await redis_client.flushdb()
    with pytest.raises(PublicationUnavailable):
        await activation.initialize(stale_accepted)
    with pytest.raises(PublicationUnavailable):
        await activation.activate(_authority(claim))
    assert not await redis_client.keys("a13n:run-stream:*")
