from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from a13n_service.presentation import (
    RUN_REPLAY_CONTENT_TYPE,
    RetainedItem,
    RetainedRunStreamEvent,
    RunOutputItemContent,
    RunReplayIntegrityError,
    RunReplaySnapshot,
    RunReplayStore,
    RunReplayUnavailable,
    RunStreamEvent,
    run_replay_key,
    run_stream_key_digest,
)
from a13n_service.storage import ObjectStore
from pydantic import ValidationError

TENANT_ID = "org_1234567890abcdef"
RUN_ID = "run_1234567890abcdef"
THREAD_ID = "thread-1234567890abcdef1234567890abcdef"
ATTEMPT_ID = "rat_1234567890abcdef"
ITEM_ID = "item_1234567890abcdef"
NOW = datetime(2026, 9, 4, 9, tzinfo=UTC)

pytestmark = pytest.mark.anyio


def _snapshot() -> RunReplaySnapshot:
    opened = RetainedRunStreamEvent(
        stream_id="0-1",
        event=RunStreamEvent(
            event_id="rse_1234567890abcdef",
            event_type="a13n.run_stream.opened",
            run_id=RUN_ID,
            thread_id=THREAD_ID,
            run_attempt_id=ATTEMPT_ID,
            harness_run_id="harness-run-1",
            occurred_at=NOW,
            payload={"schema_version": "1"},
        ),
    )
    finished = RetainedRunStreamEvent(
        stream_id="1-0",
        event=RunStreamEvent(
            event_id="rse_abcdef1234567890",
            event_type="RUN_FINISHED",
            run_id=RUN_ID,
            thread_id=THREAD_ID,
            run_attempt_id=ATTEMPT_ID,
            harness_run_id="harness-run-1",
            item_id=ITEM_ID,
            occurred_at=NOW + timedelta(seconds=1),
            payload={"type": "RUN_FINISHED", "result": {"answer": 42}},
        ),
    )
    content = RunOutputItemContent(
        result_digest="a" * 64,
        output={"answer": 42},
    )
    return RunReplaySnapshot(
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        stream_key_digest_sha256=run_stream_key_digest(TENANT_ID, RUN_ID),
        first_stream_id=opened.stream_id,
        last_stream_id=finished.stream_id,
        closed_at=NOW + timedelta(seconds=2),
        source_run_attempt_ids=(ATTEMPT_ID,),
        events=(opened, finished),
        items=(
            RetainedItem(
                id=ITEM_ID,
                kind="run_output",
                state="completed",
                first_stream_id=finished.stream_id,
                last_stream_id=finished.stream_id,
                content=content.as_json(),
            ),
        ),
    )


async def test_replay_snapshot_is_create_only_idempotent_and_item_addressable(
    object_store: ObjectStore,
) -> None:
    store = RunReplayStore(object_store)
    snapshot = _snapshot()

    assert await store.create(TENANT_ID, snapshot) == snapshot
    assert await store.create(TENANT_ID, snapshot) == snapshot
    assert await store.read(TENANT_ID, RUN_ID, expected_thread_id=THREAD_ID) == snapshot
    assert await store.read_item(TENANT_ID, RUN_ID, ITEM_ID) == snapshot.items[0]


async def test_replay_snapshot_rejects_conflicting_create_and_corrupt_storage(
    object_store: ObjectStore,
) -> None:
    store = RunReplayStore(object_store)
    snapshot = _snapshot()
    await store.create(TENANT_ID, snapshot)

    with pytest.raises(RunReplayIntegrityError, match="different snapshot"):
        await store.create(
            TENANT_ID,
            snapshot.model_copy(update={"closed_at": snapshot.closed_at + timedelta(seconds=1)}),
        )

    key = run_replay_key(TENANT_ID, RUN_ID)
    await object_store.put(
        key,
        b"{}",
        content_type=RUN_REPLAY_CONTENT_TYPE,
        metadata={"schema-version": "1", "run-id": RUN_ID, "digest-sha256": "0" * 64},
    )
    with pytest.raises(RunReplayIntegrityError):
        await store.read(TENANT_ID, RUN_ID)


async def test_replay_snapshot_missing_and_tenant_mismatch_are_explicit(
    object_store: ObjectStore,
) -> None:
    store = RunReplayStore(object_store)

    with pytest.raises(RunReplayUnavailable, match="unavailable"):
        await store.read(TENANT_ID, RUN_ID)
    with pytest.raises(RunReplayIntegrityError, match="authorized tenant"):
        await store.create(
            "org_abcdef1234567890",
            _snapshot(),
        )


def test_replay_snapshot_requires_exact_event_item_and_attempt_coverage() -> None:
    snapshot = _snapshot()

    with pytest.raises(ValidationError, match="exactly cover"):
        RunReplaySnapshot.model_validate(snapshot.model_dump(mode="python") | {"items": ()})
    with pytest.raises(ValidationError, match="source RunAttempts"):
        RunReplaySnapshot.model_validate(
            snapshot.model_dump(mode="python") | {"source_run_attempt_ids": ("rat_abcdef1234567890",)}
        )


def test_null_run_output_item_preserves_its_selected_inline_representation() -> None:
    content = RunOutputItemContent(result_digest="a" * 64, output=None)

    assert content.as_json() == {
        "schema_version": "1",
        "result_digest": "a" * 64,
        "output": None,
    }
