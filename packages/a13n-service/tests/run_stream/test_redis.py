from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
from a13n_service.run_stream.domain import (
    PublicationBackpressure,
    PublicationPending,
    PublicationRejected,
    PublicationUnavailable,
)
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


async def test_append_is_idempotent_and_pages_forward(any_redis_client: Redis) -> None:
    stream = RedisRunStream(any_redis_client, max_events=5)
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


async def test_trim_reports_explicit_replay_gap_and_blocks_snapshot(any_redis_client: Redis) -> None:
    stream = RedisRunStream(any_redis_client, max_events=2)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    first = await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)
    second = await stream.append(ORGANIZATION_ID, _event(2), attempt_number=1)
    third = await stream.append(ORGANIZATION_ID, _event(3), attempt_number=1)
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW)

    before = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    assert len(before.items) == 5 and not before.trimmed
    await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=third, finalized=True)
    with pytest.raises(RunStreamReplayGap) as captured:
        await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    # A cursor at the last removed entry still has a continuous suffix.
    suffix = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=first, limit=10)
    assert [entry.stream_id for entry in suffix.items] == [second, third]
    assert captured.value.retained_floor == second
    assert captured.value.high_watermark == third
    with pytest.raises(RetainedReplayUnavailable, match="trimmed"):
        await stream.complete_source(ORGANIZATION_ID, RUN_ID)


async def test_close_is_idempotent_and_rejects_late_append(any_redis_client: Redis) -> None:
    stream = RedisRunStream(any_redis_client)
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


async def test_successor_activation_is_atomic_idempotent_and_fences_every_old_write(any_redis_client: Redis) -> None:
    stream = RedisRunStream(any_redis_client)
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
    assert all([await any_redis_client.ttl(key) == -1 for key in await any_redis_client.keys("a13n:run-stream:*")])


@pytest.mark.parametrize("lost", ["events", "metadata", "both", "incarnation"])
async def test_state_loss_fails_closed(any_redis_client: Redis, lost: str) -> None:
    from a13n_service.run_stream.redis import _keys

    stream = RedisRunStream(any_redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    events, metadata = _keys(ORGANIZATION_ID, RUN_ID)
    if lost == "incarnation":
        await any_redis_client.hset(metadata, "server_id", "previous-primary")
    else:
        await any_redis_client.delete(*(dict(events=[events], metadata=[metadata], both=[events, metadata])[lost]))
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


async def test_snapshot_requires_projection_marker_before_closure(any_redis_client: Redis) -> None:
    stream = RedisRunStream(any_redis_client)
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


async def test_missing_projection_marker_blocks_snapshot(any_redis_client: Redis) -> None:
    stream = RedisRunStream(any_redis_client)
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


async def test_trimmed_event_keeps_active_deduplication_evidence(any_redis_client: Redis) -> None:
    stream = RedisRunStream(any_redis_client, max_events=1)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    first = await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)
    second = await stream.append(ORGANIZATION_ID, _event(2), attempt_number=1)
    await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=second, finalized=False)
    assert await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1) == first
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=second, limit=10)
    assert not page.items and page.high_watermark == second


@pytest.mark.parametrize("failure_point", ["event", "receipts", "retention"])
async def test_partial_activation_blocks_all_admission_until_exact_retry(
    any_redis_client: Redis,
    failure_point: str,
) -> None:
    from a13n_service.run_stream.redis import _SCRIPT, _keys

    stream = RedisRunStream(any_redis_client, max_events=1)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    broken = publication_failure("activate", after=failure_point)
    stream._script = any_redis_client.register_script(broken)
    leased = opening_event(RUN_ID, THREAD_ID, attempt_id="rat_2222222222222222", number=2)
    with pytest.raises(PublicationUnavailable):
        await stream.activate(ORGANIZATION_ID, leased, attempt_number=2, reason="lease_expired", allow_create=True)
    stream._script = any_redis_client.register_script(_SCRIPT)
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
    rows = await any_redis_client.xrange(events_key)
    assert len(rows) == 4 and rows[-1][0].decode() == result.recovery_stream_id
    assert await any_redis_client.hget(metadata_key, "pending_events") == b"4"
    assert await any_redis_client.hget(metadata_key, "pending") is None


async def test_lost_activation_acknowledgement_retries_without_duplicate_opening(any_redis_client: Redis) -> None:
    from redis.exceptions import ConnectionError

    stream = RedisRunStream(any_redis_client)
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


async def test_append_racing_activation_is_before_opening_or_rejected(any_redis_client: Redis) -> None:
    import asyncio

    stream = RedisRunStream(any_redis_client)
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


async def test_bootstrap_refuses_primary_change_after_durable_authority_read(any_redis_client: Redis) -> None:
    stream = RedisRunStream(any_redis_client)
    with pytest.raises(PublicationUnavailable):
        await stream.initialize(
            ORGANIZATION_ID, opening_event(RUN_ID, THREAD_ID), allow_create=True, expected_server_id="previous-primary"
        )
    assert not await any_redis_client.keys("a13n:run-stream:*")


async def test_retirement_rejects_foreign_metadata_without_mutating_keys(any_redis_client: Redis) -> None:
    from a13n_service.run_stream.redis import _keys

    stream = RedisRunStream(any_redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    events, metadata = _keys(ORGANIZATION_ID, RUN_ID)
    await any_redis_client.hset(metadata, "organization_id", "org_2222222222222222")
    before = await any_redis_client.hgetall(metadata)
    rows = await any_redis_client.xrange(events)

    with pytest.raises(RunStreamError, match="another resource"):
        await stream.retire(ORGANIZATION_ID, RUN_ID, closed_at=NOW)

    assert await any_redis_client.hgetall(metadata) == before
    assert await any_redis_client.xrange(events) == rows
    assert await any_redis_client.ttl(events) == await any_redis_client.ttl(metadata) == -1


async def test_backpressure_releases_only_after_durable_acknowledgement(any_redis_client: Redis) -> None:
    stream = RedisRunStream(any_redis_client, max_events=1, max_pending_events=3, backpressure_timeout_seconds=0)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    first = await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)
    with pytest.raises(PublicationBackpressure):
        await stream.append(ORGANIZATION_ID, _event(2), attempt_number=1)
    # Deduplicated retries consume no backlog and a rejected write leaves no barrier.
    assert await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1) == first
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    assert len(page.items) == 3 and not page.trimmed
    await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=first, finalized=False)
    second = await stream.append(ORGANIZATION_ID, _event(2), attempt_number=1)
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=first, limit=10)
    assert page.next_stream_id == second


async def test_close_waits_for_final_display_before_expiry(any_redis_client: Redis) -> None:
    from a13n_service.run_stream.redis import _keys

    stream = RedisRunStream(any_redis_client, closed_ttl_seconds=60)
    opening = await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW)
    keys = _keys(ORGANIZATION_ID, RUN_ID)
    assert all([await any_redis_client.ttl(key) == -1 for key in keys])
    await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=opening.leased_stream_id, finalized=True)
    assert all([0 < await any_redis_client.ttl(key) <= 60 for key in keys])


async def test_terminal_recovery_expiry_is_absolute_and_survives_late_mutations(any_redis_client: Redis) -> None:
    from a13n_service.run_stream.redis import _keys

    stream = RedisRunStream(any_redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    keys = _keys(ORGANIZATION_ID, RUN_ID)
    deadline = datetime.now(UTC) + timedelta(minutes=1)
    await stream.schedule_expiry(ORGANIZATION_ID, RUN_ID, deadline=deadline)
    # Delayed observations cannot PERSIST a stream after its SQL-owned deadline
    # has been installed; cleanup retries cannot renew that deadline either.
    await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)
    await stream.schedule_expiry(ORGANIZATION_ID, RUN_ID, deadline=deadline + timedelta(days=1))
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW)
    assert all([await any_redis_client.expiretime(key) == int(deadline.timestamp()) for key in keys])
    await stream.schedule_expiry(ORGANIZATION_ID, RUN_ID, deadline=datetime.now(UTC) - timedelta(seconds=1))
    assert all([not await any_redis_client.exists(key) for key in keys])
    await stream.schedule_expiry(ORGANIZATION_ID, RUN_ID, deadline=deadline)
    assert all([not await any_redis_client.exists(key) for key in keys])


async def test_expired_initialization_cannot_recreate_a_source(any_redis_client: Redis) -> None:
    from a13n_service.run_stream.domain import PublicationContinuityLost
    from a13n_service.run_stream.redis import _keys

    stream = RedisRunStream(any_redis_client)
    with pytest.raises(PublicationContinuityLost):
        await stream.initialize(
            ORGANIZATION_ID,
            opening_event(RUN_ID, THREAD_ID),
            allow_create=True,
            expected_server_id=await stream.server_incarnation(),
            recovery_deadline=datetime.now(UTC) - timedelta(seconds=1),
        )
    assert all([not await any_redis_client.exists(key) for key in _keys(ORGANIZATION_ID, RUN_ID)])


async def test_late_retirement_and_projection_preserve_shorter_replay_expiry(any_redis_client: Redis) -> None:
    from a13n_service.run_stream.redis import _keys

    stream = RedisRunStream(any_redis_client, closed_ttl_seconds=60)
    opening = await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    deadline = datetime.now(UTC) + timedelta(days=1)
    await stream.schedule_expiry(ORGANIZATION_ID, RUN_ID, deadline=deadline)
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW)
    await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=opening.leased_stream_id, finalized=True)
    keys = _keys(ORGANIZATION_ID, RUN_ID)
    expiries = [await any_redis_client.expiretime(key) for key in keys]
    await stream.mark_lifecycle_incomplete(ORGANIZATION_ID, RUN_ID)
    assert [await any_redis_client.expiretime(key) for key in keys] == expiries
    await stream.retire(ORGANIZATION_ID, RUN_ID, closed_at=NOW, recovery_deadline=deadline)
    assert [await any_redis_client.expiretime(key) for key in keys] == expiries


async def test_confirmed_archival_keeps_raw_replay_retention_independent(any_redis_client: Redis) -> None:
    from a13n_service.run_stream.redis import _keys

    stream = RedisRunStream(any_redis_client, closed_ttl_seconds=120)
    opening = await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    await stream.schedule_expiry(ORGANIZATION_ID, RUN_ID, deadline=deadline)
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW)
    await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=opening.leased_stream_id, finalized=True)
    keys = _keys(ORGANIZATION_ID, RUN_ID)
    expiries = [await any_redis_client.expiretime(key) for key in keys]
    assert all(expiry > int(deadline.timestamp()) for expiry in expiries)
    # A lost SQL settlement acknowledgement leaves cleanup discoverable, but
    # replay already covered by the finalized object keeps its original expiry.
    await stream.schedule_expiry(ORGANIZATION_ID, RUN_ID, deadline=deadline)
    assert [await any_redis_client.expiretime(key) for key in keys] == expiries
    await stream.retire(ORGANIZATION_ID, RUN_ID, closed_at=NOW, recovery_deadline=deadline)
    assert [await any_redis_client.expiretime(key) for key in keys] == expiries


async def test_partial_expiry_setup_is_repairable_without_extending_the_deadline(any_redis_client: Redis) -> None:
    from a13n_service.run_stream.redis import _RETIREMENT_SCRIPT, _keys

    stream = RedisRunStream(any_redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    events, metadata = _keys(ORGANIZATION_ID, RUN_ID)
    deadline = datetime.now(UTC) + timedelta(minutes=1)
    healthy = stream._retirement_script
    marker = "        local expiry = redis.call('EXPIRETIME', key)"
    assert _RETIREMENT_SCRIPT.count(marker) == 1
    stream._retirement_script = any_redis_client.register_script(
        _RETIREMENT_SCRIPT.replace(marker, "        if key == metadata then error('expiry interrupted') end\n" + marker)
    )
    with pytest.raises(PublicationUnavailable):
        await stream.schedule_expiry(ORGANIZATION_ID, RUN_ID, deadline=deadline)
    assert await any_redis_client.expiretime(events) == int(deadline.timestamp())
    assert await any_redis_client.ttl(metadata) == -1
    stream._retirement_script = healthy
    await stream.schedule_expiry(ORGANIZATION_ID, RUN_ID, deadline=deadline + timedelta(days=1))
    assert (
        await any_redis_client.expiretime(events)
        == await any_redis_client.expiretime(metadata)
        == int(deadline.timestamp())
    )


@pytest.mark.parametrize("max_events", [3, 4], ids=["at_limit", "below_limit"])
async def test_acknowledgement_within_retention_limit_avoids_bulk_reverse_reads(
    any_redis_client: Redis, max_events: int
) -> None:
    from a13n_service.run_stream.redis import _SCRIPT, _keys

    stream = RedisRunStream(any_redis_client, max_events=max_events, closed_ttl_seconds=60)
    opening = await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    last = await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)
    events, metadata = _keys(ORGANIZATION_ID, RUN_ID)
    before = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    rows = await any_redis_client.xrange(events, min=f"({opening.leased_stream_id}")
    pending_bytes = sum(len(fields[b"body"]) for _, fields in rows)
    # Enforce a Redis read budget without depending on timings or payload size.
    stream._script = any_redis_client.register_script(
        """
local call = redis.call
local redis = {call = function(command, ...)
    local args = {...}
    if command == 'XREVRANGE' and args[4] == 'COUNT' and tonumber(args[5]) > 1 then
        error('unexpected bulk reverse read within retention limit')
    end
    return call(command, ...)
end}
"""
        + _SCRIPT
    )

    for _ in range(2):
        await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=opening.leased_stream_id, finalized=False)
        page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
        assert page.items == before.items and not page.trimmed
        assert page.pending_events == 1 and page.pending_bytes == pending_bytes
        assert await any_redis_client.hget(metadata, "durable_cursor") == opening.leased_stream_id.encode()

    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW)
    for _ in range(2):
        await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=last, finalized=True)
        page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
        assert page.items == before.items and page.closed and not page.trimmed
        assert page.pending_events == page.pending_bytes == 0
        assert await any_redis_client.hget(metadata, "durable_cursor") == last.encode()
        assert await any_redis_client.hget(metadata, "pending") is None
        assert all([0 < await any_redis_client.ttl(key) <= 60 for key in (events, metadata)])


async def test_acknowledgement_never_trims_beyond_durable_cursor(any_redis_client: Redis) -> None:
    stream = RedisRunStream(any_redis_client, max_events=1)
    opening = await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    first = await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)
    second = await stream.append(ORGANIZATION_ID, _event(2), attempt_number=1)
    await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=first, finalized=False)
    await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=opening.leased_stream_id, finalized=False)
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=first, limit=10)
    assert page.retained_floor == first and page.next_stream_id == second
    with pytest.raises(PublicationUnavailable):
        await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=second, finalized=True)
    with pytest.raises(PublicationUnavailable):
        await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor="9999999999999999-0", finalized=False)


async def test_byte_bound_and_partial_acknowledgement_recovery(any_redis_client: Redis) -> None:
    from a13n_service.run_stream.redis import _SCRIPT, _keys

    stream = RedisRunStream(any_redis_client, max_events=1, max_pending_bytes=4096, backpressure_timeout_seconds=0)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    first = await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)
    with pytest.raises(PublicationBackpressure):
        await stream.append(
            ORGANIZATION_ID, _event(2).model_copy(update={"payload": {"text": "x" * 4096}}), attempt_number=1
        )
    marker = "    redis.call('HSET', metadata, 'durable_cursor', cursor,"
    stream._script = any_redis_client.register_script(
        _SCRIPT.replace(marker, "    error('injected failure after trim')\n" + marker)
    )
    with pytest.raises(PublicationUnavailable):
        await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=first, finalized=False)
    stream._script = any_redis_client.register_script(_SCRIPT)
    with pytest.raises(PublicationPending):
        await stream.append(ORGANIZATION_ID, _event(2), attempt_number=1)
    await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=first, finalized=False)
    await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=first, finalized=False)
    _, metadata = _keys(ORGANIZATION_ID, RUN_ID)
    assert await any_redis_client.hget(metadata, "pending_bytes") == b"0"
    assert await any_redis_client.hget(metadata, "pending_events") == b"0"
    await stream.append(ORGANIZATION_ID, _event(2), attempt_number=1)


async def test_backpressured_publisher_resumes_after_consumer_progress(any_redis_client: Redis) -> None:
    from anyio import create_task_group, sleep

    stream = RedisRunStream(any_redis_client, max_pending_events=2, backpressure_timeout_seconds=1)
    opening = await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)

    async def acknowledge() -> None:
        await sleep(0.1)
        await stream.acknowledge_display(ORGANIZATION_ID, RUN_ID, cursor=opening.leased_stream_id, finalized=False)

    async with create_task_group() as group:
        group.start_soon(acknowledge)
        cursor = await stream.append(ORGANIZATION_ID, _event(1), attempt_number=1)
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=opening.leased_stream_id, limit=10)
    assert page.next_stream_id == cursor
