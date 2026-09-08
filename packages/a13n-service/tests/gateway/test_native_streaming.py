from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from a13n_service.gateway.native_streaming import NativeRunStreamService, NativeStreamError
from a13n_service.http_errors import application_error_status
from a13n_service.run_stream import (
    RedisRunStream,
    RunReplayStore,
    RunStreamEvent,
    RunStreamReplayGap,
    deterministic_run_stream_event_id,
)
from a13n_service.storage.config import RedisMemoryConfig
from a13n_service.storage.object_store import LocalObjectStore
from a13n_service.storage.redis import open_redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.hooks.support import RUN_ID, hook_actor, seed_hook_actor_access, seed_run_and_secret
from tests.interactions.conftest import NOW, ORGANIZATION_ID, THREAD_ID

pytestmark = pytest.mark.anyio


@pytest.fixture
async def native_stream_service(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> AsyncIterator[tuple[NativeRunStreamService, RedisRunStream]]:
    await seed_run_and_secret(lifecycle_interaction_sessions)
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    async with AsyncExitStack() as stack:
        redis = await stack.enter_async_context(open_redis(RedisMemoryConfig()))
        objects = await LocalObjectStore.create(tmp_path / "objects")
        stream = RedisRunStream(redis)
        service = NativeRunStreamService(
            lifecycle_interaction_sessions,
            stream,
            RunReplayStore(objects),
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
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        occurred_at=NOW + timedelta(seconds=sequence),
        payload={"sequence": sequence},
    )


async def test_run_sse_replays_exclusively_after_cursor_and_closes(
    native_stream_service: tuple[NativeRunStreamService, RedisRunStream],
) -> None:
    service, stream = native_stream_service
    first = await stream.append(ORGANIZATION_ID, event(1))
    second = await stream.append(ORGANIZATION_ID, event(2))
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
