from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from a13n_service.run_stream import (
    RUN_REPLAY_CONTENT_TYPE,
    CompleteRunStream,
    RetainedReplayUnavailable,
    RunReplayIntegrityError,
    RunReplaySnapshot,
    RunReplayStore,
    RunStreamEntry,
    RunStreamEvent,
    deterministic_item_id,
    deterministic_run_stream_event_id,
    run_replay_key,
    run_stream_key_digest_sha256,
)
from a13n_service.storage import ObjectStore
from a13n_service.storage.codec import canonical_model_bytes

pytestmark = pytest.mark.anyio

ORGANIZATION_ID = "org_1234567890abcdef"
RUN_ID = "run_1234567890abcdef"
THREAD_ID = "thread-1234567890abcdef"
ATTEMPT_ID = "rat_1234567890abcdef"
NOW = datetime(2026, 9, 3, 8, tzinfo=UTC)


def _source(*, content: str = "hello") -> CompleteRunStream:
    item_id = deterministic_item_id(RUN_ID, "text_message", "message-1")
    entries = (
        RunStreamEntry(
            "1-0",
            RunStreamEvent(
                event_id=deterministic_run_stream_event_id("test", "1"),
                event_type="agui.text_message_start",
                run_id=RUN_ID,
                thread_id=THREAD_ID,
                run_attempt_id=ATTEMPT_ID,
                harness_run_id="harness-run-1",
                item_id=item_id,
                occurred_at=NOW,
                payload={"item_kind": "text_message", "message_id": "message-1"},
            ),
        ),
        RunStreamEntry(
            "2-0",
            RunStreamEvent(
                event_id=deterministic_run_stream_event_id("test", "2", content),
                event_type="agui.text_message_end",
                run_id=RUN_ID,
                thread_id=THREAD_ID,
                run_attempt_id=ATTEMPT_ID,
                harness_run_id="harness-run-1",
                item_id=item_id,
                occurred_at=NOW,
                payload={
                    "item_kind": "text_message",
                    "item_state": "completed",
                    "message_id": "message-1",
                    "content": content,
                },
            ),
        ),
    )
    return CompleteRunStream(entries, NOW, run_stream_key_digest_sha256(ORGANIZATION_ID, RUN_ID))


async def test_publishes_and_validates_create_only_complete_snapshot(object_store: ObjectStore) -> None:
    store = RunReplayStore(object_store)

    created = await store.publish(ORGANIZATION_ID, RUN_ID, _source())
    replayed = await store.publish(ORGANIZATION_ID, RUN_ID, _source())
    loaded = await store.read(ORGANIZATION_ID, RUN_ID)

    assert created == replayed == loaded
    assert created.source_run_attempt_ids == (ATTEMPT_ID,)
    assert len(created.items) == 1
    assert created.items[0].state == "completed"
    assert (created.items[0].first_stream_id, created.items[0].last_stream_id) == ("1-0", "2-0")


async def test_rejects_conflicting_existing_snapshot(object_store: ObjectStore) -> None:
    store = RunReplayStore(object_store)
    await store.publish(ORGANIZATION_ID, RUN_ID, _source())

    with pytest.raises(RunReplayIntegrityError, match="does not match"):
        await store.publish(ORGANIZATION_ID, RUN_ID, _source(content="different"))


@pytest.mark.parametrize("corruption", ["attempt_index", "item_index"])
async def test_rejects_semantically_inconsistent_snapshot_body(
    object_store: ObjectStore,
    corruption: str,
) -> None:
    store = RunReplayStore(object_store)
    snapshot = await store.publish(ORGANIZATION_ID, RUN_ID, _source())
    if corruption == "attempt_index":
        corrupted = snapshot.model_copy(update={"source_run_attempt_ids": ()})
    else:
        corrupted = snapshot.model_copy(update={"items": ()})
    await _overwrite_snapshot(object_store, corrupted)

    with pytest.raises(RunReplayIntegrityError, match="index does not match"):
        await store.read(ORGANIZATION_ID, RUN_ID)


@pytest.mark.parametrize("corruption", ["event_order", "event_identity"])
async def test_rejects_invalid_snapshot_event_sequence(
    object_store: ObjectStore,
    corruption: str,
) -> None:
    store = RunReplayStore(object_store)
    snapshot = await store.publish(ORGANIZATION_ID, RUN_ID, _source())
    if corruption == "event_order":
        events = tuple(reversed(snapshot.events))
        corrupted = snapshot.model_copy(
            update={
                "events": events,
                "first_stream_id": events[0].stream_id,
                "last_stream_id": events[-1].stream_id,
            }
        )
        expected = "strictly ordered"
    else:
        duplicate = snapshot.events[1].model_copy(
            update={
                "event": snapshot.events[1].event.model_copy(update={"event_id": snapshot.events[0].event.event_id})
            }
        )
        corrupted = snapshot.model_copy(update={"events": (snapshot.events[0], duplicate)})
        expected = "duplicate event identities"
    await _overwrite_snapshot(object_store, corrupted)

    with pytest.raises(RunReplayIntegrityError, match=expected):
        await store.read(ORGANIZATION_ID, RUN_ID)


async def test_marks_open_item_interrupted_at_closed_stream_boundary(object_store: ObjectStore) -> None:
    source = _source()
    incomplete = CompleteRunStream(
        entries=source.entries[:1],
        closed_at=source.closed_at,
        stream_key_digest_sha256=source.stream_key_digest_sha256,
    )

    snapshot = await RunReplayStore(object_store).publish(ORGANIZATION_ID, RUN_ID, incomplete)

    assert len(snapshot.items) == 1
    assert snapshot.items[0].state == "interrupted"


async def test_rejects_empty_or_oversized_snapshot_source(object_store: ObjectStore) -> None:
    source = _source()
    empty = CompleteRunStream((), source.closed_at, source.stream_key_digest_sha256)
    with pytest.raises(RetainedReplayUnavailable, match="event count"):
        await RunReplayStore(object_store).publish(ORGANIZATION_ID, RUN_ID, empty)
    with pytest.raises(RetainedReplayUnavailable, match="encoded size"):
        await RunReplayStore(object_store, max_bytes=10).publish(ORGANIZATION_ID, RUN_ID, source)


async def _overwrite_snapshot(object_store: ObjectStore, snapshot: RunReplaySnapshot) -> None:
    body = canonical_model_bytes(snapshot)
    await object_store.put(
        run_replay_key(ORGANIZATION_ID, RUN_ID),
        body,
        content_type=RUN_REPLAY_CONTENT_TYPE,
        metadata={
            "schema-version": "1",
            "run-id": RUN_ID,
            "digest-sha256": hashlib.sha256(body).hexdigest(),
        },
    )
