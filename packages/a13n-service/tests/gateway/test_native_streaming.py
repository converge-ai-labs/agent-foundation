from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from a13n_service.gateway.native_streaming import NativeRunStreamService, NativeStreamError
from a13n_service.gateway.queries import NativeInteractionQueries
from a13n_service.http_errors import application_error_status
from a13n_service.run_stream import (
    CompleteRunStream,
    RedisRunStream,
    RunReplayStore,
    RunStreamEvent,
    RunStreamReplayGap,
    deterministic_item_id,
    deterministic_run_stream_event_id,
    run_stream_key_digest_sha256,
)
from a13n_service.storage.config import RedisMemoryConfig
from a13n_service.storage.object_store import LocalObjectStore
from a13n_service.storage.redis import open_redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.hooks.support import RUN_ID, hook_actor, seed_hook_actor_access, seed_run_and_secret
from tests.interactions.conftest import NOW, ORGANIZATION_ID, THREAD_ID
from tests.run_stream.support import ATTEMPT_ID, activate_stream

pytestmark = pytest.mark.anyio


@pytest.fixture
async def native_replay(tmp_path) -> RunReplayStore:
    objects = await LocalObjectStore.create(tmp_path / "objects")
    return RunReplayStore(objects)


@pytest.fixture
async def native_stream_service(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    native_replay: RunReplayStore,
) -> AsyncIterator[tuple[NativeRunStreamService, RedisRunStream]]:
    await seed_run_and_secret(lifecycle_interaction_sessions)
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    async with AsyncExitStack() as stack:
        redis = await stack.enter_async_context(open_redis(RedisMemoryConfig()))
        stream = RedisRunStream(redis)
        service = NativeRunStreamService(
            lifecycle_interaction_sessions,
            stream,
            native_replay,
            page_size=100,
            poll_interval_seconds=0.001,
            heartbeat_interval_seconds=1,
            authorization_interval_seconds=1,
            maximum_lifetime_seconds=1,
        )
        yield service, stream


def event(sequence: int) -> RunStreamEvent:
    return RunStreamEvent(
        event_id=deterministic_run_stream_event_id("gateway-test", str(sequence)),
        event_type="agui.custom",
        run_attempt_id=ATTEMPT_ID,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        occurred_at=NOW + timedelta(seconds=sequence),
        payload={"sequence": sequence},
    )


async def test_run_sse_replays_exclusively_after_cursor_and_closes(
    native_stream_service: tuple[NativeRunStreamService, RedisRunStream],
) -> None:
    service, stream = native_stream_service
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    first = await stream.append(ORGANIZATION_ID, event(1), attempt_number=1)
    second = await stream.append(ORGANIZATION_ID, event(2), attempt_number=1)
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW + timedelta(seconds=3))

    attachment = await service.attach(actor=hook_actor(), run_id=RUN_ID, after_stream_id=first)
    frames = [value async for value in service.events(attachment)]

    assert attachment.closed
    assert len(frames) == 1
    assert frames[0].startswith(f"id: {second}\nevent: agui.custom\n".encode())
    assert b'"sequence":2' in frames[0]


async def test_run_sse_rejects_invalid_cursor(
    native_stream_service: tuple[NativeRunStreamService, RedisRunStream],
) -> None:
    service, _stream = native_stream_service

    with pytest.raises(NativeStreamError) as captured:
        await service.attach(actor=hook_actor(), run_id=RUN_ID, after_stream_id="not-a-cursor")

    assert captured.value.code == "invalid_cursor"
    assert application_error_status(captured.value) == 400


async def test_run_sse_conceals_unauthorized_resource(
    native_stream_service: tuple[NativeRunStreamService, RedisRunStream],
) -> None:
    service, _stream = native_stream_service
    actor = hook_actor().__class__(
        principal=hook_actor().principal,
        auth_method="session",
        credential_id="ses_other",
        boundary_workspace_id="ws_other",
    )

    with pytest.raises(NativeStreamError) as captured:
        await service.attach(actor=actor, run_id=RUN_ID, after_stream_id=None)

    assert captured.value.code == "resource_not_found"


async def test_terminal_run_without_retained_replay_reports_gap(
    native_stream_service: tuple[NativeRunStreamService, RedisRunStream],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _stream = native_stream_service
    monkeypatch.setattr(service, "_authorize", AsyncMock(return_value=(ORGANIZATION_ID, True)))

    with pytest.raises(NativeStreamError) as captured:
        await service.attach(actor=hook_actor(), run_id=RUN_ID, after_stream_id=None)

    assert captured.value.code == "run_stream_replay_gap"
    assert application_error_status(captured.value) == 409


async def test_live_replay_gap_emits_service_event_and_closes(
    native_stream_service: tuple[NativeRunStreamService, RedisRunStream],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, stream = native_stream_service
    attachment = await service.attach(actor=hook_actor(), run_id=RUN_ID, after_stream_id=None)
    monkeypatch.setattr(
        stream,
        "read",
        AsyncMock(side_effect=RunStreamReplayGap(retained_floor=None, high_watermark=None)),
    )

    frames = [frame async for frame in service.events(attachment)]

    assert len(frames) == 1
    assert frames[0].startswith(b"event: a13n.service.replay_gap\ndata: ")
    assert b'"event_type":"a13n.service.replay_gap"' in frames[0]


async def test_native_recovery_uses_source_cursor_and_is_not_repeated_after_boundary(native_stream_service):
    service, stream = native_stream_service
    first = await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    successor = await activate_stream(
        stream,
        ORGANIZATION_ID,
        RUN_ID,
        THREAD_ID,
        attempt_id="rat_2222222222222222",
        number=2,
        reason="planned_handoff",
    )
    last = await stream.append(
        ORGANIZATION_ID, event(2).model_copy(update={"run_attempt_id": "rat_2222222222222222"}), attempt_number=2
    )
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW + timedelta(seconds=3))
    attachment = await service.attach(actor=hook_actor(), run_id=RUN_ID, after_stream_id=first.leased_stream_id)
    frames = [frame async for frame in service.events(attachment)]
    assert len(frames) == 3
    assert frames[0].startswith(f"id: {successor.leased_stream_id}\nevent: run_attempt.leased\n".encode())
    assert frames[1].startswith(f"id: {successor.recovery_stream_id}\nevent: run.recovery\n".encode())
    assert b'"reason":"planned_handoff"' in frames[1]
    resumed = await service.attach(actor=hook_actor(), run_id=RUN_ID, after_stream_id=successor.recovery_stream_id)
    assert [frame async for frame in service.events(resumed)] == frames[2:]
    assert frames[2].startswith(f"id: {last}\nevent: agui.custom\n".encode())


@pytest.mark.parametrize("retained", [False, True])
async def test_legacy_live_and_retained_events_and_items_hide_internal_fields(
    native_stream_service,
    native_replay: RunReplayStore,
    lifecycle_interaction_sessions,
    monkeypatch: pytest.MonkeyPatch,
    retained: bool,
) -> None:
    service, stream = native_stream_service
    opening = await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    reference = {
        "object_key": "organizations/private/run/output.json",
        "digest_sha256": "a" * 64,
        "size_bytes": 256,
        "content_type": "application/json",
        "schema_version": "1",
    }
    worker = event(10).model_copy(
        update={
            "event_type": "run_attempt.failed",
            "lifecycle_event_id": "lev_1111111111111111",
            "payload": {
                "actor_type": "worker",
                "actor_id": "wrk_private",
                "data": {
                    "worker_id": "wrk_private",
                    "worker_build_id": "build-1",
                },
            },
        }
    )
    completed = event(11).model_copy(
        update={
            "event_type": "run.completed",
            "lifecycle_event_id": "lev_2222222222222222",
            "item_id": deterministic_item_id(RUN_ID, "run_output", RUN_ID),
            "payload": {
                "actor_type": "worker",
                "actor_id": "wrk_private",
                "data": {
                    "output_object": reference,
                },
                "item_kind": "run_output",
                "item_state": "completed",
                "content": reference,
            },
        }
    )
    positions = []
    for source in (worker, completed):
        positions.append(await stream.append_lifecycle(ORGANIZATION_ID, source))
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW + timedelta(seconds=20))
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=100)
    await native_replay.publish(
        ORGANIZATION_ID,
        RUN_ID,
        CompleteRunStream(
            entries=page.items,
            closed_at=NOW + timedelta(seconds=20),
            stream_key_digest_sha256=run_stream_key_digest_sha256(ORGANIZATION_ID, RUN_ID),
        ),
    )
    if retained:
        monkeypatch.setattr(
            stream,
            "read",
            AsyncMock(
                side_effect=RunStreamReplayGap(
                    retained_floor=None,
                    high_watermark=None,
                )
            ),
        )
    attachment = await service.attach(
        actor=hook_actor(),
        run_id=RUN_ID,
        after_stream_id=opening.leased_stream_id,
    )
    frames = [frame.decode() async for frame in service.events(attachment)]
    assert len(frames) == 2
    assert all("wrk_private" not in frame and "object_key" not in frame for frame in frames)
    assert frames[0].startswith(f"id: {positions[0]}\nevent: run_attempt.failed\n")
    payloads = [json.loads(frame.split("data: ", 1)[1])["payload"] for frame in frames]
    assert payloads[0]["data"] == {"worker_build_id": "build-1"}
    assert payloads[1]["content"]["size_bytes"] == 256
    assert payloads[1]["actor_id"] is None

    queries = NativeInteractionQueries(lifecycle_interaction_sessions, native_replay)
    items = await queries.items(actor=hook_actor(), run_id=RUN_ID, limit=50, cursor=None)
    assert len(items.items) == 1
    assert items.items[0].content == {key: value for key, value in reference.items() if key != "object_key"}
    # Public reads do not rewrite immutable replay or break its integrity checks.
    snapshot = await native_replay.read(ORGANIZATION_ID, RUN_ID)
    assert snapshot.items[0].content == reference
