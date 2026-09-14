"""Repeated Environment preparation occurrences and ambiguous publication acknowledgements."""

from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_service.interactions.environment_observation import EnvironmentHookObservation
from a13n_service.run_stream import RedisRunStream, RunStreamEvent
from a13n_service.run_stream.attempt_projection import AttemptRunStreamProjector
from tests.run_stream.support import activate_stream
from tests.run_stream.test_agui import (
    ATTEMPT_ID,
    HARNESS_RUN_ID,
    NOW,
    ORGANIZATION_ID,
    RUN_ID,
    THREAD_ID,
    _attempt_context,
)

pytestmark = pytest.mark.anyio


def observation():
    return EnvironmentHookObservation(
        event_type="environment.preparation.ready",
        thread_id=THREAD_ID,
        harness_run_id=HARNESS_RUN_ID,
        mount_id="workspace",
        occurred_at=NOW,
        payload={"mount_id": "workspace", "provider_key": "test.provider"},
    )


@pytest.mark.parametrize("elapsed", [timedelta(0), timedelta(seconds=1)])
async def test_repeated_preparation_is_distinct_across_flushes(redis_client, elapsed):
    stream = RedisRunStream(redis_client)
    opening = await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    projector = AttemptRunStreamProjector(stream, _attempt_context())
    first = observation()
    for index in range(3):
        projector.project_environment(replace(first, occurred_at=NOW + elapsed * index))
        await projector._flush_environment()
    await projector.close()

    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=opening.leased_stream_id, limit=10)
    assert len(page.items) == 3
    assert len({entry.event.event_id for entry in page.items}) == 3
    assert [entry.event.occurred_at for entry in page.items] == [NOW + elapsed * index for index in range(3)]
    assert all(entry.event.run_attempt_id == ATTEMPT_ID for entry in page.items)
    assert all(
        entry.event.event_type == first.event_type and entry.event.payload == first.payload for entry in page.items
    )


async def test_lost_acknowledgement_reuses_event_before_publishing_next_occurrence(redis_client, monkeypatch):
    stream = RedisRunStream(redis_client)
    opening = await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    projector = AttemptRunStreamProjector(stream, _attempt_context())
    original = stream.append
    dispatched: list[RunStreamEvent] = []

    async def lose_first_acknowledgement(organization_id, event, *, attempt_number):
        dispatched.append(event)
        result = await original(organization_id, event, attempt_number=attempt_number)
        if len(dispatched) == 1:
            raise TimeoutError("Lost acknowledgement after Redis committed the event")
        return result

    monkeypatch.setattr(stream, "append", lose_first_acknowledgement)
    first = observation()
    projector.project_environment(first)
    with pytest.raises(TimeoutError, match="Lost acknowledgement"):
        await projector._flush_environment()
    projector.project_environment(replace(first, occurred_at=NOW + timedelta(seconds=1)))
    await projector.close()

    assert len(dispatched) == 3
    assert dispatched[0] == dispatched[1]
    assert dispatched[2].event_id != dispatched[0].event_id
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=opening.leased_stream_id, limit=10)
    assert [entry.event for entry in page.items] == [dispatched[0], dispatched[2]]
