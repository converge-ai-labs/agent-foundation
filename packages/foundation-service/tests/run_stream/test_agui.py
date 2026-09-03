from __future__ import annotations

from datetime import UTC, datetime

import pytest
from a13n_harness import HarnessEvent
from a13n_service.run_stream import RedisRunStream, RunStreamHarnessProjector
from pydantic_ai.messages import AgentStreamEvent, PartEndEvent, PartStartEvent, TextPart
from redis.asyncio import Redis

pytestmark = pytest.mark.anyio

TENANT_ID = "org_1234567890abcdef"
RUN_ID = "run_1234567890abcdef"
THREAD_ID = "thread-1234567890abcdef"
ATTEMPT_ID = "rat_1234567890abcdef"
HARNESS_RUN_ID = "harness-run-1"
NOW = datetime(2026, 9, 3, 8, tzinfo=UTC)


def _harness_event(sequence: int, event: AgentStreamEvent) -> HarnessEvent:
    return HarnessEvent(
        thread_id=THREAD_ID,
        run_id=HARNESS_RUN_ID,
        sequence=sequence,
        occurred_at=NOW,
        event=event,
    )


async def test_projects_agui_observations_with_stable_item_identity(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    projector = RunStreamHarnessProjector(
        stream,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        run_attempt_id=ATTEMPT_ID,
        harness_run_id=HARNESS_RUN_ID,
    )

    await projector.project(_harness_event(0, PartStartEvent(index=0, part=TextPart("hello"))))
    await projector.project(_harness_event(1, PartEndEvent(index=0, part=TextPart("hello"))))
    page = await stream.read(TENANT_ID, RUN_ID, after_stream_id=None, limit=10)

    assert tuple(entry.event.event_type for entry in page.items) == (
        "agui.text_message_start",
        "agui.text_message_content",
        "agui.text_message_end",
    )
    assert len({entry.event.item_id for entry in page.items}) == 1
    assert all(entry.event.run_attempt_id == ATTEMPT_ID for entry in page.items)
    assert page.items[-1].event.payload["item_state"] == "completed"


async def test_rejects_harness_correlation_change(redis_client: Redis) -> None:
    projector = RunStreamHarnessProjector(
        RedisRunStream(redis_client),
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        run_attempt_id=ATTEMPT_ID,
        harness_run_id=HARNESS_RUN_ID,
    )
    mismatched = HarnessEvent(
        thread_id=THREAD_ID,
        run_id="harness-run-other",
        sequence=0,
        occurred_at=NOW,
        event=PartStartEvent(index=0, part=TextPart("hello")),
    )

    with pytest.raises(ValueError, match="does not match"):
        await projector.project(mismatched)
