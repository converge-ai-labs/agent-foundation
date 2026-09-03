from __future__ import annotations

from datetime import UTC, datetime

import pytest
from a13n_harness import (
    HarnessEvent,
    HarnessRunResult,
    HarnessRunResultEvent,
    HarnessState,
)
from a13n_service.interactions.codec import decode_canonical_model
from a13n_service.presentation import (
    RUN_STREAM_FIELD,
    RUN_STREAM_OPEN_ID,
    RunStreamEvent,
    RunStreamProjectionError,
    RunStreamProjector,
    run_stream_key,
)
from pydantic import TypeAdapter
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    ToolReturnPart,
)
from pydantic_ai.usage import RunUsage
from redis.asyncio import Redis

TENANT_ID = "org_1234567890abcdef"
RUN_ID = "run_1234567890abcdef"
THREAD_ID = "thread-1234567890abcdef1234567890abcdef"
ATTEMPT_ID = "rat_1234567890abcdef"
HARNESS_RUN_ID = "harness-run-1"
NOW = datetime(2026, 9, 4, 10, tzinfo=UTC)
_EVENT_ADAPTER = TypeAdapter(RunStreamEvent)

pytestmark = pytest.mark.anyio


def _event(sequence: int, event: object, *, thread_id: str = THREAD_ID) -> HarnessEvent:
    return HarnessEvent(
        thread_id=thread_id,
        run_id=HARNESS_RUN_ID,
        sequence=sequence,
        occurred_at=NOW,
        event=event,
    )


def _terminal(sequence: int, output: object) -> HarnessRunResultEvent[object]:
    return HarnessRunResultEvent(
        thread_id=THREAD_ID,
        run_id=HARNESS_RUN_ID,
        sequence=sequence,
        occurred_at=NOW,
        result=HarnessRunResult(
            thread_id=THREAD_ID,
            run_id=HARNESS_RUN_ID,
            status="completed",
            output=output,
            state=HarnessState.new(thread_id=THREAD_ID),
            usage=RunUsage(),
        ),
    )


def _projector(redis_client: Redis, **limits: int) -> RunStreamProjector:
    return RunStreamProjector(
        redis_client,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        run_attempt_id=ATTEMPT_ID,
        **limits,
    )


async def _stored_events(redis_client: Redis) -> tuple[tuple[str, RunStreamEvent], ...]:
    entries = await redis_client.xrange(run_stream_key(TENANT_ID, RUN_ID))
    return tuple(
        (
            stream_id.decode() if isinstance(stream_id, bytes) else stream_id,
            decode_canonical_model(fields[RUN_STREAM_FIELD], _EVENT_ADAPTER),
        )
        for stream_id, fields in entries
    )


async def test_run_stream_projects_stable_items_and_bounded_terminal_output(
    redis_client: Redis,
) -> None:
    projector = _projector(redis_client, max_event_bytes=1024)

    started = await projector.project(_event(0, PartStartEvent(index=0, part=TextPart("hello"))))
    ended = await projector.project(_event(1, PartEndEvent(index=0, part=TextPart("hello"))))
    terminal = await projector.project(_terminal(2, "x" * 4096))

    assert len(started) == 2
    assert {event.item_id for event in (*started, *ended)} == {started[0].item_id}
    assert started[0].item_id is not None
    assert terminal[0].event_type == "RUN_FINISHED"
    assert terminal[0].item_id is not None
    assert terminal[0].item_id != started[0].item_id
    assert terminal[0].payload["result"] is None
    assert terminal[0].payload["rawEvent"]["result_omitted"] is True

    stored = await _stored_events(redis_client)
    assert stored[0][0] == RUN_STREAM_OPEN_ID
    assert [event for _, event in stored[1:]] == [*started, *ended, *terminal]
    assert all(
        len(fields[RUN_STREAM_FIELD]) <= 1024
        for _, fields in await redis_client.xrange(run_stream_key(TENANT_ID, RUN_ID))
    )


async def test_event_and_item_identity_are_stable_across_publication_retry(
    redis_client: Redis,
) -> None:
    source = _event(0, PartStartEvent(index=0, part=TextPart("hello")))

    first = await _projector(redis_client).project(source)
    replay = await _projector(redis_client).project(source)

    assert [(event.event_id, event.item_id) for event in replay] == [(event.event_id, event.item_id) for event in first]


async def test_trimmed_opening_marker_is_an_explicit_replay_gap(redis_client: Redis) -> None:
    await _projector(redis_client, max_entries=2).project(_event(0, PartStartEvent(index=0, part=TextPart("hello"))))

    stored = await _stored_events(redis_client)
    assert stored[0][0] != RUN_STREAM_OPEN_ID
    with pytest.raises(RunStreamProjectionError, match="outside the retained horizon"):
        await _projector(redis_client, max_entries=2).project(_event(1, PartEndEvent(index=0, part=TextPart("hello"))))


async def test_oversized_nonterminal_event_fails_before_stream_creation(
    redis_client: Redis,
) -> None:
    source = _event(
        0,
        FunctionToolResultEvent(
            ToolReturnPart(
                tool_name="large_result",
                content="x" * 4096,
                tool_call_id="call-1",
            )
        ),
    )

    with pytest.raises(RunStreamProjectionError, match="exceeds the configured event limit"):
        await _projector(redis_client, max_event_bytes=512).project(source)
    assert await redis_client.xlen(run_stream_key(TENANT_ID, RUN_ID)) == 0


async def test_harness_thread_mismatch_fails_before_observation(redis_client: Redis) -> None:
    with pytest.raises(RunStreamProjectionError, match="Thread"):
        await _projector(redis_client).project(
            _event(
                0,
                PartStartEvent(index=0, part=TextPart("hello")),
                thread_id="thread-abcdef1234567890abcdef1234567890",
            )
        )
    assert await redis_client.xlen(run_stream_key(TENANT_ID, RUN_ID)) == 0
