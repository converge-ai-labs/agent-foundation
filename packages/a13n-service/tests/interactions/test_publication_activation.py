from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_service.interactions.attempts import AttemptAuthorityError
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.lifecycle import LifecycleEventRecord
from a13n_service.run_stream import RedisRunStream, RunStreamEvent, deterministic_run_stream_event_id
from a13n_service.run_stream.activation import PublicationActivator
from a13n_service.run_stream.domain import PublicationRejected, PublicationUnavailable
from a13n_service.run_stream.events import lifecycle_stream_event
from a13n_service.storage import short_session
from sqlalchemy import select

from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, ORGANIZATION_ID
from .test_attempt_execution import _accept_root, _authority, _worker

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("first_activated", [False, True])
async def test_durable_claim_and_activation_are_separate_ordered_boundaries(
    relational_interaction_sessions,
    interaction_object_store,
    redis_client,
    first_activated,
):
    sessions = relational_interaction_sessions
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
    relational_interaction_sessions,
    interaction_object_store,
    redis_client,
):
    sessions = relational_interaction_sessions
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
