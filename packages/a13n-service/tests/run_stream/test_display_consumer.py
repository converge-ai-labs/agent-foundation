from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import anyio
import pytest
from a13n_service.run_stream import RedisRunStream
from a13n_service.run_stream.display_candidates import DisplayCandidate, DisplayCandidates, DisplaySettlement
from a13n_service.run_stream.display_consumer import DisplayConsumerPolicy, RunDisplayConsumer
from a13n_service.run_stream.display_store import RunDisplayStore
from a13n_service.run_stream.domain import (
    PublicationUnavailable,
    RunStreamEvent,
    deterministic_item_id,
    deterministic_run_stream_event_id,
)
from a13n_service.run_stream.redis import _keys
from a13n_service.storage import ObjectStore, ObjectStoreUnavailable
from redis.asyncio import Redis
from tests.run_stream.support import activate_stream

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 9, 17, tzinfo=UTC)
CANDIDATE = DisplayCandidate("org_test", "run_test", "thread_test")


def _delta(index: int) -> RunStreamEvent:
    return RunStreamEvent(
        event_id=deterministic_run_stream_event_id("display-consumer", str(index)),
        event_type="agui.text_message_content",
        run_id=CANDIDATE.run_id,
        thread_id=CANDIDATE.thread_id,
        run_attempt_id="rat_1234567890abcdef",
        harness_run_id="harness_test",
        item_id=deterministic_item_id(CANDIDATE.run_id, "text_message", "message"),
        occurred_at=NOW,
        payload={"item_kind": "text_message", "messageId": "message", "delta": "x"},
    )


def _candidates() -> AsyncMock:
    candidates = AsyncMock(spec=DisplayCandidates)
    candidates.settlement.return_value = DisplaySettlement(True, None, False)
    return candidates


async def test_continuous_consumption_exceeds_raw_horizon_and_finalizes_after_settlement(
    redis_client: Redis,
    object_store: ObjectStore,
) -> None:
    stream = RedisRunStream(redis_client, max_events=8)
    store = RunDisplayStore(object_store)
    candidates = _candidates()
    await activate_stream(stream, CANDIDATE.organization_id, CANDIDATE.run_id, CANDIDATE.thread_id)
    policy = DisplayConsumerPolicy(flush_events=64)
    for batch in range(21):
        for index in range(batch * 200, (batch + 1) * 200):
            cursor = await stream.append(CANDIDATE.organization_id, _delta(index), attempt_number=1)
        # A flush may stop at its event, byte, or time bound before reaching the tail.
        # Every flush restores progress through a fresh process-local consumer.
        with anyio.fail_after(30):
            while True:
                stored = await RunDisplayConsumer(candidates, stream, store, policy=policy).consume_run(CANDIDATE)
                assert stored is not None
                assert stored.snapshot.complete and not stored.snapshot.finalized
                if stored.snapshot.cursor == cursor:
                    break
        assert stored.snapshot.items[0].content["text"] == "x" * ((batch + 1) * 200)
        assert stored.snapshot.items[0].state == "in_progress"
        assert stored.snapshot.complete and not stored.snapshot.finalized
    events_key, metadata_key = _keys(CANDIDATE.organization_id, CANDIDATE.run_id)
    assert await redis_client.xlen(events_key) == 8
    await stream.close(CANDIDATE.organization_id, CANDIDATE.run_id, closed_at=NOW)
    consumer = RunDisplayConsumer(candidates, stream, store)
    assert not (await consumer.consume_run(CANDIDATE)).snapshot.finalized
    candidates.settlement.return_value = DisplaySettlement(True, NOW, False)
    final = await consumer.consume_run(CANDIDATE)
    assert final is not None and final.snapshot.finalized and final.snapshot.complete
    assert final.snapshot.items[0].state == "interrupted"
    deadline = await redis_client.hget(metadata_key, "retention_deadline")
    assert await consumer.consume_run(CANDIDATE) == final
    assert await redis_client.hget(metadata_key, "retention_deadline") == deadline
    await redis_client.delete(events_key, metadata_key)
    assert await consumer.consume_run(CANDIDATE) == final


async def test_failed_publication_preserves_suffix_and_lost_redis_ack_recovers_without_duplicates(
    redis_client: Redis,
    object_store: ObjectStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = RedisRunStream(redis_client, max_events=1)
    store = RunDisplayStore(object_store)
    consumer = RunDisplayConsumer(_candidates(), stream, store)
    await activate_stream(stream, CANDIDATE.organization_id, CANDIDATE.run_id, CANDIDATE.thread_id)
    await stream.append(CANDIDATE.organization_id, _delta(1), attempt_number=1)
    previous = await consumer.consume_run(CANDIDATE)
    cursor = await stream.append(CANDIDATE.organization_id, _delta(2), attempt_number=1)
    publish = store.publish
    monkeypatch.setattr(store, "publish", AsyncMock(side_effect=ObjectStoreUnavailable("offline")))
    with pytest.raises(ObjectStoreUnavailable):
        await consumer.consume_run(CANDIDATE)
    assert (
        await store.read(CANDIDATE.organization_id, CANDIDATE.run_id, expected_thread_id=CANDIDATE.thread_id)
        == previous
    )
    page = await stream.read(
        CANDIDATE.organization_id, CANDIDATE.run_id, after_stream_id=previous.snapshot.cursor, limit=10
    )
    assert page.next_stream_id == cursor
    monkeypatch.setattr(store, "publish", publish)
    acknowledge = stream.acknowledge_display
    calls = 0

    async def lost_ack(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PublicationUnavailable("lost ack after PUT")
        return await acknowledge(*args, **kwargs)

    monkeypatch.setattr(stream, "acknowledge_display", lost_ack)
    with pytest.raises(PublicationUnavailable):
        await consumer.consume_run(CANDIDATE)
    recovered = await RunDisplayConsumer(_candidates(), stream, store).consume_run(CANDIDATE)
    assert recovered.snapshot.cursor == cursor
    assert recovered.snapshot.items[0].content["text"] == "xx"


async def test_gap_preserves_committed_items_and_requires_durable_incomplete_retirement(
    redis_client: Redis,
    object_store: ObjectStore,
) -> None:
    stream = RedisRunStream(redis_client)
    candidates = _candidates()
    consumer = RunDisplayConsumer(candidates, stream, RunDisplayStore(object_store))
    await activate_stream(stream, CANDIDATE.organization_id, CANDIDATE.run_id, CANDIDATE.thread_id)
    cursor = await stream.append(CANDIDATE.organization_id, _delta(1), attempt_number=1)
    await consumer.consume_run(CANDIDATE)
    events_key, metadata_key = _keys(CANDIDATE.organization_id, CANDIDATE.run_id)
    await redis_client.delete(events_key)
    partial = await consumer.consume_run(CANDIDATE)
    assert not partial.snapshot.complete and not partial.snapshot.finalized
    assert partial.snapshot.cursor == cursor and partial.snapshot.items[0].content["text"] == "x"
    await stream.retire(CANDIDATE.organization_id, CANDIDATE.run_id, closed_at=NOW)
    assert await redis_client.ttl(metadata_key) == -1
    candidates.settlement.return_value = DisplaySettlement(True, NOW, True)
    final = await consumer.consume_run(CANDIDATE)
    assert final.snapshot.finalized and not final.snapshot.complete
    assert final.snapshot.cursor == cursor and final.snapshot.items[0].state == "interrupted"
    assert await redis_client.ttl(metadata_key) > 0


async def test_display_limit_never_commits_omitted_content(redis_client: Redis, object_store: ObjectStore) -> None:
    stream = RedisRunStream(redis_client)
    store = RunDisplayStore(object_store, max_bytes=1024)
    candidates = _candidates()
    consumer = RunDisplayConsumer(candidates, stream, store)
    await activate_stream(stream, CANDIDATE.organization_id, CANDIDATE.run_id, CANDIDATE.thread_id)
    previous = await consumer.consume_run(CANDIDATE)
    await stream.append(CANDIDATE.organization_id, _delta(1), attempt_number=1)
    bounded = RunDisplayConsumer(candidates, stream, store, policy=DisplayConsumerPolicy(max_snapshot_bytes=1024))
    partial = await bounded.consume_run(CANDIDATE)
    assert partial.snapshot.cursor == previous.snapshot.cursor
    assert partial.snapshot.items == () and partial.snapshot.incomplete_reason == "display_limit_exceeded"

    await stream.retire(CANDIDATE.organization_id, CANDIDATE.run_id, closed_at=NOW)
    candidates.settlement.return_value = DisplaySettlement(True, NOW, True)
    finalized = await bounded.consume_run(CANDIDATE)
    assert finalized.snapshot.finalized and not finalized.snapshot.complete
    assert finalized.snapshot.cursor == previous.snapshot.cursor
