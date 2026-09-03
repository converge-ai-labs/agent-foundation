from __future__ import annotations

from datetime import UTC, datetime

import pytest
from a13n_harness import HarnessEvent, HarnessRunResult, HarnessRunResultEvent, HarnessState, SafeFailure
from a13n_service.run_stream import RedisRunStream, RunStreamHarnessProjector
from pydantic_ai.messages import (
    AgentStreamEvent,
    FunctionToolResultEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import RunUsage
from redis.asyncio import Redis

pytestmark = pytest.mark.anyio

TENANT_ID = "org_1234567890abcdef"
RUN_ID = "run_1234567890abcdef"
THREAD_ID = "thread-1234567890abcdef1234567890abcdef"
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
        "item.completed",
    )
    assert len({entry.event.item_id for entry in page.items}) == 1
    assert all(entry.event.run_attempt_id == ATTEMPT_ID for entry in page.items)
    assert page.items[-1].event.payload["item_state"] == "completed"
    assert page.items[-1].event.payload["first_stream_id"] == page.items[0].stream_id
    assert page.items[-1].event.payload["last_content_stream_id"] == page.items[-2].stream_id


@pytest.mark.parametrize(
    ("status", "expected_type", "failure"),
    [
        ("completed", "agui.run_finished", None),
        ("failed", "agui.run_error", SafeFailure(code="model_failed", message="The model failed.")),
        ("cancelled", "agui.run_error", None),
    ],
)
async def test_projects_terminal_harness_result(
    redis_client: Redis,
    status: str,
    expected_type: str,
    failure: SafeFailure | None,
) -> None:
    stream = RedisRunStream(redis_client)
    projector = RunStreamHarnessProjector(
        stream,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        run_attempt_id=ATTEMPT_ID,
        harness_run_id=HARNESS_RUN_ID,
    )
    result = HarnessRunResult(
        thread_id=THREAD_ID,
        run_id=HARNESS_RUN_ID,
        status=status,
        output="done" if status == "completed" else None,
        state=HarnessState.new(thread_id=THREAD_ID) if status == "completed" else None,
        usage=RunUsage(),
        failure=failure,
    )

    await projector.project(
        HarnessRunResultEvent(
            thread_id=THREAD_ID,
            run_id=HARNESS_RUN_ID,
            sequence=0,
            occurred_at=NOW,
            result=result,
        )
    )
    page = await stream.read(TENANT_ID, RUN_ID, after_stream_id=None, limit=10)

    assert tuple(entry.event.event_type for entry in page.items) == (expected_type,)
    assert page.items[0].event.item_id is None


async def test_tool_item_closes_only_after_successful_result(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    projector = RunStreamHarnessProjector(
        stream,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        run_attempt_id=ATTEMPT_ID,
        harness_run_id=HARNESS_RUN_ID,
    )

    await projector.project(
        _harness_event(
            0,
            PartEndEvent(
                index=0,
                part=ToolCallPart(tool_name="search", args={"q": "x"}, tool_call_id="call-1"),
            ),
        )
    )
    before_result = await stream.read(TENANT_ID, RUN_ID, after_stream_id=None, limit=10)
    assert tuple(entry.event.event_type for entry in before_result.items) == (
        "agui.tool_call_start",
        "agui.tool_call_args",
        "agui.tool_call_end",
    )

    await projector.project(
        _harness_event(
            1,
            FunctionToolResultEvent(
                ToolReturnPart(
                    tool_name="search",
                    content={"found": 1},
                    tool_call_id="call-1",
                )
            ),
        )
    )
    page = await stream.read(TENANT_ID, RUN_ID, after_stream_id=None, limit=10)

    assert tuple(entry.event.event_type for entry in page.items[-2:]) == (
        "agui.tool_call_result",
        "item.completed",
    )
    assert len({entry.event.item_id for entry in page.items}) == 1


async def test_failed_tool_result_emits_safe_item_failure(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    projector = RunStreamHarnessProjector(
        stream,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        run_attempt_id=ATTEMPT_ID,
        harness_run_id=HARNESS_RUN_ID,
    )

    await projector.project(
        _harness_event(
            0,
            FunctionToolResultEvent(
                ToolReturnPart(
                    tool_name="search",
                    content="provider-private-body",
                    tool_call_id="call-failed",
                    outcome="failed",
                )
            ),
        )
    )
    page = await stream.read(TENANT_ID, RUN_ID, after_stream_id=None, limit=10)

    assert tuple(entry.event.event_type for entry in page.items) == ("agui.custom", "item.failed")
    assert page.items[-1].event.payload["failure"] == {
        "code": "tool_result_failed",
        "message": "The tool returned a failed presentation outcome.",
    }
    assert "provider-private-body" not in str(page.items[-1].event.payload)


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
