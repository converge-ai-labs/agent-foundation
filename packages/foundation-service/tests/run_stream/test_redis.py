from __future__ import annotations

from datetime import UTC, datetime

import pytest
from a13n_service.run_stream import (
    RedisRunStream,
    RetainedReplayUnavailable,
    RunStreamClosed,
    RunStreamError,
    RunStreamEvent,
    RunStreamReplayGap,
    deterministic_run_stream_event_id,
)
from redis.asyncio import Redis

pytestmark = pytest.mark.anyio

TENANT_ID = "org_1234567890abcdef"
RUN_ID = "run_1234567890abcdef"
THREAD_ID = "thread-1234567890abcdef"
NOW = datetime(2026, 9, 3, 8, tzinfo=UTC)


def _event(sequence: int, *, event_type: str = "agui.custom") -> RunStreamEvent:
    return RunStreamEvent(
        event_id=deterministic_run_stream_event_id("test", str(sequence)),
        event_type=event_type,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        run_attempt_id="rat_1234567890abcdef",
        harness_run_id="harness-run-1",
        occurred_at=NOW,
        payload={"sequence": sequence},
    )


def test_rejects_oversized_event_payload() -> None:
    values = _event(1).model_dump(mode="python")
    values["payload"] = {"value": "x" * (256 * 1024)}
    with pytest.raises(ValueError, match="payload exceeds"):
        RunStreamEvent.model_validate(values)


async def test_append_is_idempotent_and_pages_forward(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client, max_events=3)
    first = await stream.append(TENANT_ID, _event(1))
    assert await stream.append(TENANT_ID, _event(1)) == first
    with pytest.raises(RunStreamError, match="reused with different content"):
        await stream.append(TENANT_ID, _event(1).model_copy(update={"payload": {"sequence": 99}}))
    second = await stream.append(TENANT_ID, _event(2))

    page = await stream.read(TENANT_ID, RUN_ID, after_stream_id=first, limit=1)

    assert tuple(entry.event.payload for entry in page.items) == ({"sequence": 2},)
    assert page.next_stream_id == second
    assert page.retained_floor == first
    assert page.high_watermark == second
    assert not page.closed
    assert not page.trimmed


async def test_trim_reports_explicit_replay_gap_and_blocks_snapshot(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client, max_events=2)
    first = await stream.append(TENANT_ID, _event(1))
    second = await stream.append(TENANT_ID, _event(2))
    third = await stream.append(TENANT_ID, _event(3))
    await stream.close(TENANT_ID, RUN_ID, closed_at=NOW)

    with pytest.raises(RunStreamReplayGap) as captured:
        await stream.read(TENANT_ID, RUN_ID, after_stream_id=first, limit=10)
    assert captured.value.retained_floor == second
    assert captured.value.high_watermark == third
    with pytest.raises(RetainedReplayUnavailable, match="trimmed"):
        await stream.complete_source(TENANT_ID, RUN_ID)


async def test_close_is_idempotent_and_rejects_late_append(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    entry_id = await stream.append(TENANT_ID, _event(1))

    await stream.close(TENANT_ID, RUN_ID, closed_at=NOW)
    await stream.close(TENANT_ID, RUN_ID, closed_at=NOW)
    assert await stream.append(TENANT_ID, _event(1)) == entry_id

    page = await stream.read(TENANT_ID, RUN_ID, after_stream_id=None, limit=10)
    assert page.closed
    assert page.next_stream_id == entry_id
    source = await stream.complete_source(TENANT_ID, RUN_ID)
    assert tuple(entry.stream_id for entry in source.entries) == (entry_id,)
    with pytest.raises(RunStreamClosed):
        await stream.append(TENANT_ID, _event(2))


async def test_one_open_stream_spans_attempt_generations_without_ttl(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    first_attempt = _event(1)
    second_attempt = _event(2).model_copy(update={"run_attempt_id": "rat_2222222222222222"})

    await stream.append(TENANT_ID, first_attempt)
    await stream.append(TENANT_ID, second_attempt)

    page = await stream.read(TENANT_ID, RUN_ID, after_stream_id=None, limit=10)
    assert tuple(entry.event.run_attempt_id for entry in page.items) == (
        "rat_1234567890abcdef",
        "rat_2222222222222222",
    )
    keys = await redis_client.keys("a13n:run-stream:*")
    assert keys
    ttls = [await redis_client.ttl(key) for key in keys]
    assert all(ttl == -1 for ttl in ttls)


async def test_snapshot_requires_each_running_attempt_projection_marker(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    running = _event(1, event_type="run_attempt.running").model_copy(
        update={"payload": {"data": {"harness_run_id": "harness-run-1"}}}
    )
    await stream.append(TENANT_ID, running)
    await stream.close(TENANT_ID, RUN_ID, closed_at=NOW)

    with pytest.raises(RetainedReplayUnavailable, match="live presentation projection is incomplete"):
        await stream.complete_source(TENANT_ID, RUN_ID)

    await stream.complete_attempt_projection(
        TENANT_ID,
        RUN_ID,
        run_attempt_id="rat_1234567890abcdef",
        harness_run_id="harness-run-1",
    )
    source = await stream.complete_source(TENANT_ID, RUN_ID)
    assert source.entries[0].event == running

    with pytest.raises(RunStreamError, match="identity changed"):
        await stream.complete_attempt_projection(
            TENANT_ID,
            RUN_ID,
            run_attempt_id="rat_1234567890abcdef",
            harness_run_id="harness-run-other",
        )
