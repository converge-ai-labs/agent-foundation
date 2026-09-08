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
from a13n_service.run_stream.domain import PublicationPending, PublicationRejected, PublicationUnavailable
from redis.asyncio import Redis
from tests.run_stream.support import activate_stream, opening_event, publication_failure

pytestmark = pytest.mark.anyio

ORGANIZATION_ID = "org_1234567890abcdef"
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
    stream = RedisRunStream(redis_client, max_events=5)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    first = await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)
    assert await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1) == first
    with pytest.raises(RunStreamError, match="reused with different content"):
        await stream.append(
            ORGANIZATION_ID, _event(1).model_copy(update={"payload": {"sequence": 99}}), attempt_number=1
        )
    second = await stream.append(ORGANIZATION_ID, _event(2), attempt_number=1)

    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=first, limit=1)

    assert tuple(entry.event.payload for entry in page.items) == ({"sequence": 2},)
    assert page.next_stream_id == second
    assert page.retained_floor != first
    assert page.high_watermark == second
    assert not page.closed
    assert not page.trimmed


async def test_trim_reports_explicit_replay_gap_and_blocks_snapshot(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client, max_events=2)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    first = await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)
    second = await stream.append(ORGANIZATION_ID, _event(2), attempt_number=1)
    third = await stream.append(ORGANIZATION_ID, _event(3), attempt_number=1)
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW)

    with pytest.raises(RunStreamReplayGap) as captured:
        await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=first, limit=10)
    assert captured.value.retained_floor == second
    assert captured.value.high_watermark == third
    with pytest.raises(RetainedReplayUnavailable, match="trimmed"):
        await stream.complete_source(ORGANIZATION_ID, RUN_ID)


async def test_close_is_idempotent_and_rejects_late_append(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    entry_id = await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)

    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW)
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW)
    with pytest.raises(RunStreamClosed):
        await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)

    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    assert page.closed
    assert page.next_stream_id == entry_id
    source = await stream.complete_source(ORGANIZATION_ID, RUN_ID)
    assert tuple(entry.stream_id for entry in source.entries[2:]) == (entry_id,)
    with pytest.raises(RunStreamClosed):
        await stream.append(ORGANIZATION_ID, _event(2), attempt_number=1)


async def test_successor_activation_is_atomic_idempotent_and_fences_every_old_write(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    first = await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    old = _event(1)
    await stream.append(ORGANIZATION_ID, old, attempt_number=1)
    successor = "rat_2222222222222222"
    opening = await activate_stream(
        stream, ORGANIZATION_ID, RUN_ID, THREAD_ID, attempt_id=successor, number=2, reason="lease_expired"
    )
    assert (
        await activate_stream(
            stream, ORGANIZATION_ID, RUN_ID, THREAD_ID, attempt_id=successor, number=2, reason="lease_expired"
        )
        == opening
    )
    for operation in (
        stream.append(ORGANIZATION_ID, old, attempt_number=1),
        stream.mark_incomplete(ORGANIZATION_ID, RUN_ID, run_attempt_id=old.run_attempt_id, attempt_number=1),
        stream.complete_attempt_projection(
            ORGANIZATION_ID, RUN_ID, run_attempt_id=old.run_attempt_id, attempt_number=1, harness_run_id="harness-run-1"
        ),
    ):
        with pytest.raises(PublicationRejected):
            await operation
    receipt = await stream.activation_result(
        ORGANIZATION_ID, opening_event(RUN_ID, THREAD_ID, attempt_id=old.run_attempt_id), attempt_number=1, reason=None
    )
    assert receipt is not None and not receipt.active
    assert receipt.leased_stream_id == first.leased_stream_id
    await stream.append(ORGANIZATION_ID, _event(2).model_copy(update={"run_attempt_id": successor}), attempt_number=2)
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    assert [entry.event.event_type for entry in page.items] == [
        "run.accepted",
        "run_attempt.leased",
        "agui.custom",
        "run_attempt.leased",
        "run.recovery",
        "agui.custom",
    ]
    assert page.items[3].stream_id == opening.leased_stream_id
    assert page.items[4].stream_id == opening.recovery_stream_id
    assert page.items[4].event.payload == {"reason": "lease_expired"}
    assert all([await redis_client.ttl(key) == -1 for key in await redis_client.keys("a13n:run-stream:*")])


@pytest.mark.parametrize("lost", ["events", "metadata", "both", "incarnation"])
async def test_state_loss_fails_closed(redis_client: Redis, lost: str) -> None:
    from a13n_service.run_stream.redis import _keys

    stream = RedisRunStream(redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    events, metadata = _keys(ORGANIZATION_ID, RUN_ID)
    if lost == "incarnation":
        await redis_client.hset(metadata, "server_id", "previous-primary")
    else:
        await redis_client.delete(*(dict(events=[events], metadata=[metadata], both=[events, metadata])[lost]))
    with pytest.raises(PublicationUnavailable):
        await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)
    with pytest.raises(PublicationUnavailable):
        await stream.initialize(
            ORGANIZATION_ID,
            opening_event(RUN_ID, THREAD_ID),
            allow_create=False,
            expected_server_id=await stream.server_incarnation(),
        )
    with pytest.raises(PublicationUnavailable):
        await stream.activate(
            ORGANIZATION_ID,
            opening_event(RUN_ID, THREAD_ID, attempt_id="rat_2222222222222222", number=2),
            attempt_number=2,
            reason="planned_handoff",
            allow_create=True,
        )


async def test_snapshot_requires_projection_marker_before_closure(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    running = _event(1, event_type="run_attempt.running").model_copy(
        update={"payload": {"data": {"harness_run_id": "harness-run-1"}}, "lifecycle_event_id": "evt_1234567890abcdef"}
    )
    await stream.append_lifecycle(ORGANIZATION_ID, running)
    await stream.complete_attempt_projection(
        ORGANIZATION_ID, RUN_ID, run_attempt_id=running.run_attempt_id, attempt_number=1, harness_run_id="harness-run-1"
    )
    with pytest.raises(RunStreamError, match="identity changed"):
        await stream.complete_attempt_projection(
            ORGANIZATION_ID, RUN_ID, run_attempt_id=running.run_attempt_id, attempt_number=1, harness_run_id="other"
        )
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW)
    source = await stream.complete_source(ORGANIZATION_ID, RUN_ID)
    assert source.entries[-1].event == running


async def test_missing_projection_marker_blocks_snapshot(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    running = _event(1, event_type="run_attempt.running").model_copy(
        update={"lifecycle_event_id": "evt_1234567890abcdef"}
    )
    await stream.append_lifecycle(ORGANIZATION_ID, running)
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW)
    with pytest.raises(RetainedReplayUnavailable, match="live presentation projection is incomplete"):
        await stream.complete_source(ORGANIZATION_ID, RUN_ID)
    with pytest.raises(RunStreamClosed):
        await stream.complete_attempt_projection(
            ORGANIZATION_ID,
            RUN_ID,
            run_attempt_id=running.run_attempt_id,
            attempt_number=1,
            harness_run_id="harness-run-1",
        )


async def test_trimmed_event_keeps_active_deduplication_evidence(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client, max_events=1)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    first = await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)
    second = await stream.append(ORGANIZATION_ID, _event(2), attempt_number=1)
    assert await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1) == first
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=second, limit=10)
    assert not page.items and page.high_watermark == second


@pytest.mark.parametrize("failure_point", ["event", "receipts", "retention"])
async def test_partial_activation_blocks_all_admission_until_exact_retry(
    redis_client: Redis,
    failure_point: str,
) -> None:
    from a13n_service.run_stream.redis import _SCRIPT, _keys

    stream = RedisRunStream(redis_client, max_events=1)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    broken = publication_failure("activate", after=failure_point)
    stream._script = redis_client.register_script(broken)
    leased = opening_event(RUN_ID, THREAD_ID, attempt_id="rat_2222222222222222", number=2)
    with pytest.raises(PublicationUnavailable):
        await stream.activate(ORGANIZATION_ID, leased, attempt_number=2, reason="lease_expired", allow_create=True)
    stream._script = redis_client.register_script(_SCRIPT)
    old_publisher_error = PublicationPending if failure_point == "event" else PublicationRejected
    with pytest.raises(old_publisher_error):
        await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)
    new_publisher_error = PublicationRejected if failure_point == "event" else PublicationPending
    with pytest.raises(new_publisher_error):
        await stream.append(
            ORGANIZATION_ID,
            _event(2).model_copy(update={"run_attempt_id": leased.run_attempt_id}),
            attempt_number=2,
        )
    with pytest.raises(PublicationUnavailable):
        await stream.activation_result(ORGANIZATION_ID, leased, attempt_number=2, reason="lease_expired")
    with pytest.raises(RunStreamReplayGap):
        await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    result = await stream.activate(ORGANIZATION_ID, leased, attempt_number=2, reason="lease_expired", allow_create=True)
    assert result.active and result.recovery_stream_id
    assert (
        await stream.activate(ORGANIZATION_ID, leased, attempt_number=2, reason="lease_expired", allow_create=False)
        == result
    )
    events_key, metadata_key = _keys(ORGANIZATION_ID, RUN_ID)
    rows = await redis_client.xrange(events_key)
    assert len(rows) == 1 and rows[0][0].decode() == result.recovery_stream_id
    assert await redis_client.hget(metadata_key, "pending") is None


async def test_lost_activation_acknowledgement_retries_without_duplicate_opening(redis_client: Redis) -> None:
    from redis.exceptions import ConnectionError

    stream = RedisRunStream(redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    script = stream._script

    async def lost_ack(**kwargs):
        await script(**kwargs)
        raise ConnectionError("response lost after execution")

    stream._script = lost_ack
    leased = opening_event(RUN_ID, THREAD_ID, attempt_id="rat_2222222222222222", number=2)
    with pytest.raises(PublicationUnavailable):
        await stream.activate(ORGANIZATION_ID, leased, attempt_number=2, reason="planned_handoff", allow_create=True)
    stream._script = script
    result = await stream.activate(
        ORGANIZATION_ID, leased, attempt_number=2, reason="planned_handoff", allow_create=True
    )
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    assert len(page.items) == 4
    assert page.items[-1].stream_id == result.recovery_stream_id


async def test_append_racing_activation_is_before_opening_or_rejected(redis_client: Redis) -> None:
    import asyncio

    stream = RedisRunStream(redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    leased = opening_event(RUN_ID, THREAD_ID, attempt_id="rat_2222222222222222", number=2)
    old_write, activation = await asyncio.gather(
        stream.append(ORGANIZATION_ID, _event(1), attempt_number=1),
        stream.activate(ORGANIZATION_ID, leased, attempt_number=2, reason="lease_expired", allow_create=True),
        return_exceptions=True,
    )
    assert not isinstance(activation, BaseException)
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    types = [entry.event.event_type for entry in page.items]
    if isinstance(old_write, BaseException):
        assert isinstance(old_write, PublicationRejected)
        assert types == ["run.accepted", "run_attempt.leased", "run_attempt.leased", "run.recovery"]
    else:
        assert types == ["run.accepted", "run_attempt.leased", "agui.custom", "run_attempt.leased", "run.recovery"]


async def test_bootstrap_refuses_primary_change_after_durable_authority_read(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    with pytest.raises(PublicationUnavailable):
        await stream.initialize(
            ORGANIZATION_ID, opening_event(RUN_ID, THREAD_ID), allow_create=True, expected_server_id="previous-primary"
        )
    assert not await redis_client.keys("a13n:run-stream:*")


async def test_retirement_rejects_foreign_metadata_without_mutating_keys(redis_client: Redis) -> None:
    from a13n_service.run_stream.redis import _keys

    stream = RedisRunStream(redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    events, metadata = _keys(ORGANIZATION_ID, RUN_ID)
    await redis_client.hset(metadata, "organization_id", "org_2222222222222222")
    before = await redis_client.hgetall(metadata)
    rows = await redis_client.xrange(events)

    with pytest.raises(RunStreamError, match="another resource"):
        await stream.retire(ORGANIZATION_ID, RUN_ID, closed_at=NOW)

    assert await redis_client.hgetall(metadata) == before
    assert await redis_client.xrange(events) == rows
    assert await redis_client.ttl(events) == await redis_client.ttl(metadata) == -1
