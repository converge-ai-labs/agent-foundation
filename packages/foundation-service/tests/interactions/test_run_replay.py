from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_harness import HarnessEvent, HarnessRunResult, HarnessRunResultEvent, HarnessState
from a13n_service.interactions import (
    AttemptScheduler,
    CompletedOutcomeCandidate,
    RunPayloadEnvelope,
    RunPayloadStore,
)
from a13n_service.presentation import (
    RunOutputItemContent,
    RunReplayPublisher,
    RunReplayStore,
    RunReplayUnavailable,
    RunStreamProjector,
    run_stream_key,
)
from a13n_service.storage import ObjectStore
from pydantic_ai.messages import PartEndEvent, PartStartEvent, TextPart
from pydantic_ai.usage import RunUsage
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, TENANT_ID
from .test_attempt_execution import _accept_root, _authority, _worker
from .test_subagent_acceptance import _complete_run

pytestmark = pytest.mark.anyio

HARNESS_RUN_ID = "completed-child"


async def test_complete_run_stream_publishes_immutable_replay_and_semantic_items(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    redis_client: Redis,
) -> None:
    states, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "replay-lease",
        attempt_id_factory=lambda: "rat_replay1111111111",
    ).claim(run.id, _worker())
    assert claim is not None
    projector = _projector(redis_client, run.id, run.thread_id, claim.attempt.id)
    await projector.project(_text_event(run.thread_id, 0, PartStartEvent(index=0, part=TextPart("hello"))))
    await projector.project(_text_event(run.thread_id, 1, PartEndEvent(index=0, part=TextPart("hello"))))
    await projector.project(_terminal_event(run.thread_id, 2, {"answer": 42}))
    completed = await _complete_run(
        interaction_sessions,
        interaction_object_store,
        states,
        run,
        _authority(claim),
    )
    replays = RunReplayStore(interaction_object_store)
    publisher = RunReplayPublisher(
        interaction_sessions,
        redis_client,
        replays,
        RunPayloadStore(interaction_object_store),
        stream_ttl_seconds=60,
    )

    snapshot = await publisher.publish(tenant_id=TENANT_ID, run_id=run.id)

    assert snapshot.run_id == completed.id
    assert snapshot.source_run_attempt_ids == (claim.attempt.id,)
    assert [(item.kind, item.state) for item in snapshot.items] == [
        ("message", "completed"),
        ("run_output", "completed"),
    ]
    assert snapshot.items[0].content == {"content": "hello", "role": "assistant"}
    output = RunOutputItemContent.model_validate(snapshot.items[-1].content)
    assert output.output == {"answer": 42}
    assert output.output_object is None
    assert await publisher.publish(tenant_id=TENANT_ID, run_id=run.id) == snapshot
    assert await redis_client.ttl(run_stream_key(TENANT_ID, run.id)) > 0


async def test_object_backed_run_output_is_verified_and_retained_by_reference(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    redis_client: Redis,
) -> None:
    states, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: "large-replay-lease",
        attempt_id_factory=lambda: "rat_replay2222222222",
    ).claim(run.id, _worker())
    assert claim is not None
    output = {"answer": "x" * 4096}
    payloads = RunPayloadStore(interaction_object_store)
    output_object = await payloads.create(
        TENANT_ID,
        RunPayloadEnvelope(
            run_id=run.id,
            payload_kind="output",
            payload_schema_version="1",
            payload=output,
        ),
    )
    await _projector(
        redis_client,
        run.id,
        run.thread_id,
        claim.attempt.id,
        max_event_bytes=1024,
    ).project(_terminal_event(run.thread_id, 0, output))
    await _complete_run(
        interaction_sessions,
        interaction_object_store,
        states,
        run,
        _authority(claim),
        outcome=CompletedOutcomeCandidate(output_object=output_object),
    )

    snapshot = await RunReplayPublisher(
        interaction_sessions,
        redis_client,
        RunReplayStore(interaction_object_store),
        payloads,
        stream_ttl_seconds=60,
    ).publish(tenant_id=TENANT_ID, run_id=run.id)

    assert len(snapshot.items) == 1
    retained = snapshot.items[0]
    assert (retained.kind, retained.state) == ("run_output", "completed")
    content = RunOutputItemContent.model_validate(retained.content)
    assert content.output_object == output_object
    assert "output" not in content.model_fields_set
    assert snapshot.events[-1].event.payload["result"] is None
    assert snapshot.events[-1].event.payload["rawEvent"]["result_omitted"] is True


@pytest.mark.parametrize("failure_kind", ["trimmed", "terminal_mismatch", "multiple_terminals"])
async def test_incomplete_or_contradictory_stream_does_not_publish_replay(
    interaction_sessions: async_sessionmaker[AsyncSession],
    interaction_object_store: ObjectStore,
    redis_client: Redis,
    failure_kind: str,
) -> None:
    states, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(
        interaction_sessions,
        clock=lambda: NOW + timedelta(seconds=1),
        token_factory=lambda: f"{failure_kind}-lease",
        attempt_id_factory=lambda: "rat_replay3333333333",
    ).claim(run.id, _worker())
    assert claim is not None
    projector = _projector(
        redis_client,
        run.id,
        run.thread_id,
        claim.attempt.id,
        max_entries=2 if failure_kind == "trimmed" else 10_000,
    )
    if failure_kind == "trimmed":
        await projector.project(_text_event(run.thread_id, 0, PartStartEvent(index=0, part=TextPart("hello"))))
        terminal_output = {"answer": 42}
    elif failure_kind == "multiple_terminals":
        await projector.project(_terminal_event(run.thread_id, 0, {"answer": 41}))
        terminal_output = {"answer": 42}
    else:
        terminal_output = {"answer": 41}
    await projector.project(_terminal_event(run.thread_id, 1, terminal_output))
    await _complete_run(
        interaction_sessions,
        interaction_object_store,
        states,
        run,
        _authority(claim),
    )
    replays = RunReplayStore(interaction_object_store)

    with pytest.raises(RunReplayUnavailable):
        await RunReplayPublisher(
            interaction_sessions,
            redis_client,
            replays,
            RunPayloadStore(interaction_object_store),
            stream_ttl_seconds=60,
        ).publish(tenant_id=TENANT_ID, run_id=run.id)
    with pytest.raises(RunReplayUnavailable):
        await replays.read(TENANT_ID, run.id)


def _projector(
    redis_client: Redis,
    run_id: str,
    thread_id: str,
    attempt_id: str,
    **limits: int,
) -> RunStreamProjector:
    return RunStreamProjector(
        redis_client,
        tenant_id=TENANT_ID,
        run_id=run_id,
        thread_id=thread_id,
        run_attempt_id=attempt_id,
        **limits,
    )


def _text_event(thread_id: str, sequence: int, event: object) -> HarnessEvent:
    return HarnessEvent(
        thread_id=thread_id,
        run_id=HARNESS_RUN_ID,
        sequence=sequence,
        occurred_at=NOW + timedelta(seconds=2),
        event=event,
    )


def _terminal_event(thread_id: str, sequence: int, output: object) -> HarnessRunResultEvent[object]:
    return HarnessRunResultEvent(
        thread_id=thread_id,
        run_id=HARNESS_RUN_ID,
        sequence=sequence,
        occurred_at=NOW + timedelta(seconds=2),
        result=HarnessRunResult(
            thread_id=thread_id,
            run_id=HARNESS_RUN_ID,
            status="completed",
            output=output,
            state=HarnessState.new(thread_id=thread_id),
            usage=RunUsage(),
        ),
    )
