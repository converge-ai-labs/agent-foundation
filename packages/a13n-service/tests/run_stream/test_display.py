from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from a13n_service.run_stream.display_model import DisplayIntegrityError, DisplayLimitExceeded, RunDisplaySnapshot
from a13n_service.run_stream.display_projection import project_display
from a13n_service.run_stream.display_store import RunDisplayStore
from a13n_service.run_stream.domain import (
    RunStreamEntry,
    RunStreamEvent,
    deterministic_item_id,
    deterministic_run_stream_event_id,
)
from a13n_service.run_stream.redis import run_stream_key_digest_sha256
from a13n_service.storage import ObjectConflict, ObjectInfo, ObjectStore, ObjectStoreUnavailable
from a13n_service.storage.object_store.api import ObjectSource

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 9, 17, tzinfo=UTC)


def _empty() -> RunDisplaySnapshot:
    return RunDisplaySnapshot(
        version=1,
        run_id="run_test",
        thread_id="thread_test",
        cursor=None,
        stream_key_digest_sha256=run_stream_key_digest_sha256("org_test", "run_test"),
    )


def _delta(index: int, delta: str = "x") -> RunStreamEntry:
    return RunStreamEntry(
        f"{index}-0",
        RunStreamEvent(
            event_id=deterministic_run_stream_event_id("display-test", str(index)),
            event_type="agui.text_message_content",
            run_id="run_test",
            thread_id="thread_test",
            run_attempt_id="rat_test",
            harness_run_id="harness_test",
            item_id=deterministic_item_id("run_test", "text_message", "message"),
            occurred_at=NOW,
            payload={"item_kind": "text_message", "messageId": "message", "delta": delta},
        ),
    )


async def test_resumes_open_text_without_retaining_or_duplicating_deltas(object_store: ObjectStore) -> None:
    store = RunDisplayStore(object_store)
    initial = await store.publish("org_test", _empty(), previous=None)
    entries = tuple(_delta(index) for index in range(1, 5001))
    first = project_display(initial.snapshot, entries[:2500])
    saved = await store.publish("org_test", first, previous=initial)
    assert saved.snapshot.items[0].state == "in_progress"
    loaded = await store.read("org_test", "run_test", expected_thread_id="thread_test")
    # The entire first batch is replayed after a restart; its text appears once.
    second = project_display(loaded.snapshot, entries, closed_at=NOW)
    final = await store.publish("org_test", second, previous=loaded)
    assert final.snapshot.cursor == "5000-0"
    assert final.snapshot.items[0].content == {
        "messageId": "message",
        "text": "x" * 5000,
        "run_attempt_id": "rat_test",
        "harness_run_id": "harness_test",
    }
    assert final.snapshot.items[0].state == "interrupted"
    assert final.snapshot.complete and final.snapshot.finalized
    assert final.snapshot.source_run_attempt_ids == ("rat_test",)
    with pytest.raises(DisplayIntegrityError, match="finalized"):
        await store.publish("org_test", second.model_copy(update={"version": 4}), previous=final)


async def test_competing_consumers_cannot_overwrite_the_winner(object_store: ObjectStore) -> None:
    store = RunDisplayStore(object_store)
    previous = await store.publish("org_test", _empty(), previous=None)
    winner = await store.publish(
        "org_test", project_display(previous.snapshot, (_delta(1, "winner"),)), previous=previous
    )
    with pytest.raises(ObjectConflict):
        await store.publish("org_test", project_display(previous.snapshot, (_delta(1, "loser"),)), previous=previous)
    assert await store.read("org_test", "run_test", expected_thread_id="thread_test") == winner


async def test_oversize_update_preserves_committed_cursor(object_store: ObjectStore) -> None:
    store = RunDisplayStore(object_store, max_bytes=1200)
    previous = await store.publish("org_test", _empty(), previous=None)
    with pytest.raises(DisplayLimitExceeded):
        await store.publish("org_test", project_display(previous.snapshot, (_delta(1, "x" * 2000),)), previous=previous)
    assert await store.read("org_test", "run_test", expected_thread_id="thread_test") == previous


async def test_partial_tool_arguments_survive_serialization() -> None:
    first = _delta(1)

    def args(index: int, delta: str) -> RunStreamEntry:
        return RunStreamEntry(
            f"{index}-0",
            first.event.model_copy(
                update={
                    "event_type": "agui.tool_call_args",
                    "payload": {"item_kind": "tool_call", "delta": delta},
                }
            ),
        )

    part = project_display(_empty(), (args(1, '{"key":'),))
    restored = RunDisplaySnapshot.model_validate_json(part.model_dump_json())
    final = project_display(restored, (args(2, '"value"}'),))
    assert isinstance(final.items[0].content, dict)
    assert final.items[0].content["arguments"] == '{"key":"value"}'
    assert final.items[0].state == "in_progress"


async def test_lost_put_acknowledgement_recovers_the_committed_checkpoint(
    object_store: ObjectStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_put = object_store.put

    async def lost_ack(
        key: str,
        source: ObjectSource,
        *,
        content_type: str | None = None,
        metadata: Mapping[str, str] | None = None,
        if_none_match: bool = False,
        if_match: str | None = None,
    ) -> ObjectInfo:
        await original_put(
            key, source, content_type=content_type, metadata=metadata, if_none_match=if_none_match, if_match=if_match
        )
        raise ObjectStoreUnavailable("acknowledgement lost")

    put = AsyncMock(side_effect=lost_ack)
    monkeypatch.setattr(object_store, "put", put)
    store = RunDisplayStore(object_store)
    published = await store.publish("org_test", _empty(), previous=None)
    assert published.snapshot == _empty()
    assert put.await_count == 1


@pytest.mark.parametrize("corruption", ["cursor", "items", "attempts", "coverage"])
async def test_successor_cannot_discard_durable_progress(object_store: ObjectStore, corruption: str) -> None:
    store = RunDisplayStore(object_store)
    initial = await store.publish("org_test", _empty(), previous=None)
    previous = await store.publish("org_test", project_display(initial.snapshot, (_delta(1),)), previous=initial)
    candidate = project_display(previous.snapshot, ())
    if corruption == "cursor":
        candidate = candidate.model_copy(update={"cursor": None})
    elif corruption == "items":
        candidate = candidate.model_copy(update={"items": ()})
    elif corruption == "attempts":
        candidate = candidate.model_copy(update={"source_run_attempt_ids": ()})
    else:
        partial = project_display(previous.snapshot, (), incomplete_reason="source_gap")
        previous = await store.publish("org_test", partial, previous=previous)
        candidate = candidate.model_copy(update={"version": previous.snapshot.version + 1})
    with pytest.raises(DisplayIntegrityError):
        await store.publish("org_test", candidate, previous=previous)
    assert await store.read("org_test", "run_test", expected_thread_id="thread_test") == previous


async def test_tool_parent_survives_later_observations_without_parent_field() -> None:
    parent_id = deterministic_item_id("run_test", "text_message", "parent")
    first = _delta(1)
    start = RunStreamEntry(
        "1-0",
        first.event.model_copy(
            update={
                "event_type": "agui.tool_call_start",
                "payload": {"item_kind": "tool_call", "parent_item_id": parent_id, "toolCallName": "search"},
            }
        ),
    )
    saved = project_display(_empty(), (start,))
    delta = RunStreamEntry(
        "2-0",
        first.event.model_copy(
            update={
                "event_type": "agui.tool_call_args",
                "payload": {"item_kind": "tool_call", "delta": "{}"},
            }
        ),
    )
    resumed = project_display(RunDisplaySnapshot.model_validate_json(saved.model_dump_json()), (delta,))
    assert resumed.items[0].parent_item_id == parent_id
    assert isinstance(resumed.items[0].content, dict)
    assert resumed.items[0].content["arguments"] == "{}"
    conflicting = RunStreamEntry(
        "3-0",
        delta.event.model_copy(
            update={
                "payload": {"item_kind": "tool_call", "parent_item_id": None, "delta": "x"},
            }
        ),
    )
    with pytest.raises(DisplayIntegrityError, match="correlation changed"):
        project_display(resumed, (conflicting,))
