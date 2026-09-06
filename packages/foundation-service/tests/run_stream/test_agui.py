from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from a13n_harness import HarnessEvent, HarnessRunResult, HarnessRunResultEvent, HarnessState, SafeFailure
from a13n_service.interactions.environment_observation import EnvironmentHookObservation
from a13n_service.run_stream import (
    MAX_RUN_STREAM_PAYLOAD_BYTES,
    RedisRunStream,
    RetainedReplayUnavailable,
    RunStreamEvent,
    RunStreamHarnessProjector,
    RunStreamReplayGap,
    deterministic_run_stream_event_id,
)
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

ORGANIZATION_ID = "org_1234567890abcdef"
RUN_ID = "run_1234567890abcdef"
THREAD_ID = "thread-1234567890abcdef1234567890abcdef"
ATTEMPT_ID = "rat_1234567890abcdef"
HARNESS_RUN_ID = "harness-run-1"
NOW = datetime(2026, 9, 3, 8, tzinfo=UTC)


class _BlockingRunStream(RedisRunStream):
    def __init__(self, redis: Redis) -> None:
        super().__init__(redis)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def append(self, organization_id: str, event: RunStreamEvent) -> str:
        self.started.set()
        await self.release.wait()
        return await super().append(organization_id, event)


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
        organization_id=ORGANIZATION_ID,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        run_attempt_id=ATTEMPT_ID,
        harness_run_id=HARNESS_RUN_ID,
    )

    projector.project(_harness_event(0, PartStartEvent(index=0, part=TextPart("hello"))))
    projector.project(_harness_event(1, PartEndEvent(index=0, part=TextPart("hello"))))
    await projector.close()
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)

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
        organization_id=ORGANIZATION_ID,
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

    projector.project(
        HarnessRunResultEvent(
            thread_id=THREAD_ID,
            run_id=HARNESS_RUN_ID,
            sequence=0,
            occurred_at=NOW,
            result=result,
        )
    )
    await projector.close()
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)

    assert tuple(entry.event.event_type for entry in page.items) == (expected_type,)
    assert page.items[0].event.item_id is None


async def test_oversized_terminal_result_is_explicitly_omitted(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    projector = RunStreamHarnessProjector(
        stream,
        organization_id=ORGANIZATION_ID,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        run_attempt_id=ATTEMPT_ID,
        harness_run_id=HARNESS_RUN_ID,
    )
    projector.project(
        HarnessRunResultEvent(
            thread_id=THREAD_ID,
            run_id=HARNESS_RUN_ID,
            sequence=0,
            occurred_at=NOW,
            result=HarnessRunResult(
                thread_id=THREAD_ID,
                run_id=HARNESS_RUN_ID,
                status="completed",
                output="x" * (MAX_RUN_STREAM_PAYLOAD_BYTES + 1),
                state=HarnessState.new(thread_id=THREAD_ID),
                usage=RunUsage(),
            ),
        )
    )

    await projector.close()
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)

    assert len(page.items) == 1
    assert page.items[0].event.payload["result"] is None
    assert page.items[0].event.payload["result_omitted"] is True


async def test_tool_item_closes_only_after_successful_result(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    projector = RunStreamHarnessProjector(
        stream,
        organization_id=ORGANIZATION_ID,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        run_attempt_id=ATTEMPT_ID,
        harness_run_id=HARNESS_RUN_ID,
    )

    projector.project(
        _harness_event(
            0,
            PartEndEvent(
                index=0,
                part=ToolCallPart(tool_name="search", args={"q": "x"}, tool_call_id="call-1"),
            ),
        )
    )
    projector.project(
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
    await projector.close()
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)

    assert tuple(entry.event.event_type for entry in page.items[:3]) == (
        "agui.tool_call_start",
        "agui.tool_call_args",
        "agui.tool_call_end",
    )
    assert tuple(entry.event.event_type for entry in page.items[-2:]) == (
        "agui.tool_call_result",
        "item.completed",
    )
    assert len({entry.event.item_id for entry in page.items}) == 1


async def test_failed_tool_result_emits_safe_item_failure(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    projector = RunStreamHarnessProjector(
        stream,
        organization_id=ORGANIZATION_ID,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        run_attempt_id=ATTEMPT_ID,
        harness_run_id=HARNESS_RUN_ID,
    )

    projector.project(
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
    await projector.close()
    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)

    assert tuple(entry.event.event_type for entry in page.items) == ("agui.custom", "item.failed")
    assert page.items[-1].event.payload["failure"] == {
        "code": "tool_result_failed",
        "message": "The tool returned a failed presentation outcome.",
    }
    assert "provider-private-body" not in str(page.items[-1].event.payload)


async def test_queue_overflow_marks_stream_incomplete(redis_client: Redis) -> None:
    stream = _BlockingRunStream(redis_client)
    projector = RunStreamHarnessProjector(
        stream,
        organization_id=ORGANIZATION_ID,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        run_attempt_id=ATTEMPT_ID,
        harness_run_id=HARNESS_RUN_ID,
        max_pending_events=1,
    )
    projector.project(_harness_event(0, PartStartEvent(index=0, part=TextPart("first"))))
    await stream.started.wait()
    projector.project(_harness_event(1, PartEndEvent(index=0, part=TextPart("first"))))
    projector.project(_harness_event(2, PartStartEvent(index=1, part=TextPart("dropped"))))
    stream.release.set()

    await projector.close()
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW)

    with pytest.raises(RunStreamReplayGap):
        await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    with pytest.raises(RetainedReplayUnavailable, match="incomplete"):
        await stream.complete_source(ORGANIZATION_ID, RUN_ID)


async def test_empty_clean_attempt_still_records_projection_completion(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    projector = RunStreamHarnessProjector(
        stream,
        organization_id=ORGANIZATION_ID,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        run_attempt_id=ATTEMPT_ID,
        harness_run_id=HARNESS_RUN_ID,
    )
    await projector.close()
    await stream.append(
        ORGANIZATION_ID,
        RunStreamEvent(
            event_id=deterministic_run_stream_event_id("test", "attempt-running"),
            event_type="run_attempt.running",
            run_id=RUN_ID,
            thread_id=THREAD_ID,
            run_attempt_id=ATTEMPT_ID,
            harness_run_id=HARNESS_RUN_ID,
            occurred_at=NOW,
            payload={"data": {"harness_run_id": HARNESS_RUN_ID}},
        ),
    )
    await stream.close(ORGANIZATION_ID, RUN_ID, closed_at=NOW)

    source = await stream.complete_source(ORGANIZATION_ID, RUN_ID)
    assert len(source.entries) == 1


async def test_projects_environment_observation_with_attempt_correlation(redis_client: Redis) -> None:
    stream = RedisRunStream(redis_client)
    projector = RunStreamHarnessProjector(
        stream,
        organization_id=ORGANIZATION_ID,
        run_id=RUN_ID,
        thread_id=THREAD_ID,
        run_attempt_id=ATTEMPT_ID,
        harness_run_id=HARNESS_RUN_ID,
    )
    projector.project_environment(
        EnvironmentHookObservation(
            event_type="environment.preparation.ready",
            thread_id=THREAD_ID,
            harness_run_id=HARNESS_RUN_ID,
            mount_id="workspace",
            occurred_at=NOW,
            payload={
                "mount_id": "workspace",
                "provider_key": "test.provider",
                "operation_families": ["files"],
            },
        )
    )
    await projector.close()

    page = await stream.read(ORGANIZATION_ID, RUN_ID, after_stream_id=None, limit=10)
    assert tuple(entry.event.event_type for entry in page.items) == ("environment.preparation.ready",)
    assert page.items[0].event.run_attempt_id == ATTEMPT_ID
    assert page.items[0].event.harness_run_id == HARNESS_RUN_ID
    assert page.items[0].event.payload["provider_key"] == "test.provider"


async def test_rejects_harness_correlation_change(redis_client: Redis) -> None:
    projector = RunStreamHarnessProjector(
        RedisRunStream(redis_client),
        organization_id=ORGANIZATION_ID,
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
        projector.project(mismatched)
