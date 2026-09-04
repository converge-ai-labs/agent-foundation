from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import timedelta

import pytest
from a13n_service.gateway.hosted_agui import HostedAguiCancelRequest, HostedAguiError, HostedAguiService
from a13n_service.gateway.models import AguiRunBindingRecord, AguiThreadBindingRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.run_stream import (
    RedisRunStream,
    RunReplayStore,
    RunStreamEvent,
    deterministic_run_stream_event_id,
)
from a13n_service.storage import short_session
from a13n_service.storage.config import RedisMemoryConfig
from a13n_service.storage.object_store import LocalObjectStore
from a13n_service.storage.redis import open_redis
from ag_ui.core import RunAgentInput
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.gateway.test_commands import _commands, _complete_run, _frozen, _Preparation, _wait_run
from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import AGENT_ID, NOW

pytestmark = pytest.mark.anyio


def _request(*, run_id: str = "external-run-1", text: str = "hello") -> RunAgentInput:
    return RunAgentInput.model_validate(
        {
            "threadId": "external-thread-1",
            "runId": run_id,
            "state": {},
            "messages": [{"id": f"message-{run_id}", "role": "user", "content": text}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    )


async def _service(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path,
    stack: AsyncExitStack,
) -> tuple[HostedAguiService, RedisRunStream, LocalObjectStore]:
    objects = await LocalObjectStore.create(tmp_path / "objects")
    redis = await stack.enter_async_context(open_redis(RedisMemoryConfig()))
    stream = RedisRunStream(redis)
    return (
        HostedAguiService(
            sessions,
            _commands(sessions, objects, _Preparation(), _frozen_resolver()),
            stream,
            RunReplayStore(objects),
            page_size=100,
            poll_interval_seconds=0.001,
            heartbeat_interval_seconds=1,
            authorization_interval_seconds=1,
            maximum_lifetime_seconds=1,
            clock=lambda: NOW,
        ),
        stream,
        objects,
    )


def _frozen_resolver():
    from tests.gateway.test_commands import _Freezing

    return _Freezing([_frozen()])


async def test_initial_run_atomically_creates_external_bindings_and_replays(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    async with AsyncExitStack() as stack:
        service, _stream, _objects = await _service(lifecycle_interaction_sessions, tmp_path, stack)
        request = _request()

        first = await service.accept(actor=_actor(), agent_id=AGENT_ID, request=request, last_event_id=None)
        repeated = await service.accept(actor=_actor(), agent_id=AGENT_ID, request=request, last_event_id=None)

    assert repeated.binding == first.binding
    assert first.binding.external_thread_id == "external-thread-1"
    assert first.binding.external_run_id == "external-run-1"
    async with short_session(lifecycle_interaction_sessions) as database:
        thread_binding = await database.scalar(select(AguiThreadBindingRecord))
        run_binding = await database.scalar(select(AguiRunBindingRecord))
        run = await database.scalar(select(RunRecord).where(RunRecord.id == first.binding.run_id))
    assert thread_binding is not None
    assert run_binding is not None
    assert run is not None
    assert thread_binding.active_thread_id == run.thread_id
    assert (
        run_binding.request_json["messages"]
        == request.model_dump(mode="json", by_alias=True, exclude_none=True)["messages"]
    )


async def test_reused_external_run_id_with_different_request_conflicts(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    async with AsyncExitStack() as stack:
        service, _stream, _objects = await _service(lifecycle_interaction_sessions, tmp_path, stack)
        await service.accept(actor=_actor(), agent_id=AGENT_ID, request=_request(), last_event_id=None)

        with pytest.raises(HostedAguiError) as captured:
            await service.accept(
                actor=_actor(),
                agent_id=AGENT_ID,
                request=_request(text="different"),
                last_event_id=None,
            )

    assert captured.value.code == "agui_run_id_conflict"
    assert captured.value.status_code == 409


async def test_new_user_tail_defaults_active_waiting_run(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    async with AsyncExitStack() as stack:
        service, _stream, objects = await _service(lifecycle_interaction_sessions, tmp_path, stack)
        first = await service.accept(
            actor=_actor(),
            agent_id=AGENT_ID,
            request=_request(),
            last_event_id=None,
        )
        await _wait_run(
            lifecycle_interaction_sessions,
            objects,
            run_id=first.binding.run_id,
        )
        request = RunAgentInput.model_validate(
            {
                "threadId": "external-thread-1",
                "runId": "external-run-2",
                "parentRunId": "external-run-1",
                "state": {},
                "messages": [
                    {"id": "message-external-run-1", "role": "user", "content": "hello"},
                    {"id": "message-external-run-2", "role": "user", "content": "handle this instead"},
                ],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            }
        )

        second = await service.accept(
            actor=_actor(),
            agent_id=AGENT_ID,
            request=request,
            last_event_id=None,
        )

    async with short_session(lifecycle_interaction_sessions) as database:
        successor = await database.scalar(select(RunRecord).where(RunRecord.id == second.binding.run_id))
    assert successor is not None
    assert successor.parent_run_id == first.binding.run_id
    assert successor.input_kind == "waiting_continue"
    assert successor.input_json["resolutions"][0]["outcome"] == "reject"
    assert successor.input_json["input"]["content"] == [{"type": "text", "text": "handle this instead"}]


async def test_explicit_resume_maps_to_atomic_waiting_feedback_run(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    async with AsyncExitStack() as stack:
        service, _stream, objects = await _service(lifecycle_interaction_sessions, tmp_path, stack)
        first = await service.accept(
            actor=_actor(),
            agent_id=AGENT_ID,
            request=_request(),
            last_event_id=None,
        )
        await _wait_run(
            lifecycle_interaction_sessions,
            objects,
            run_id=first.binding.run_id,
        )
        request = RunAgentInput.model_validate(
            {
                "threadId": "external-thread-1",
                "runId": "external-run-feedback",
                "parentRunId": "external-run-1",
                "state": {},
                "messages": [{"id": "message-external-run-1", "role": "user", "content": "hello"}],
                "tools": [],
                "context": [],
                "forwardedProps": {
                    "a13n": {
                        "schema_version": "1",
                        "resume": [{"call_id": "approval-1", "action": "approve"}],
                    }
                },
            }
        )

        feedback = await service.accept(
            actor=_actor(),
            agent_id=AGENT_ID,
            request=request,
            last_event_id=None,
        )

    async with short_session(lifecycle_interaction_sessions) as database:
        successor = await database.scalar(select(RunRecord).where(RunRecord.id == feedback.binding.run_id))
        binding = await database.scalar(
            select(AguiRunBindingRecord).where(AguiRunBindingRecord.external_run_id == "external-run-feedback")
        )
    assert successor is not None
    assert binding is not None
    assert successor.parent_run_id == first.binding.run_id
    assert successor.input_kind == "waiting_feedback"
    assert successor.input_json["resolutions"] == [
        {
            "call_id": "approval-1",
            "kind": "approval",
            "outcome": "approve",
            "result": None,
        }
    ]


async def test_historical_parent_forks_and_selects_new_active_thread(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    async with AsyncExitStack() as stack:
        service, _stream, objects = await _service(lifecycle_interaction_sessions, tmp_path, stack)
        first = await service.accept(
            actor=_actor(),
            agent_id=AGENT_ID,
            request=_request(),
            last_event_id=None,
        )
        await _complete_run(
            lifecycle_interaction_sessions,
            objects,
            run_id=first.binding.run_id,
        )
        second_request = RunAgentInput.model_validate(
            {
                "threadId": "external-thread-1",
                "runId": "external-run-2",
                "parentRunId": "external-run-1",
                "state": {},
                "messages": [
                    {"id": "message-external-run-1", "role": "user", "content": "hello"},
                    {"id": "message-external-run-2", "role": "user", "content": "second"},
                ],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            }
        )
        second = await service.accept(
            actor=_actor(),
            agent_id=AGENT_ID,
            request=second_request,
            last_event_id=None,
        )
        await _complete_run(
            lifecycle_interaction_sessions,
            objects,
            run_id=second.binding.run_id,
            expected_thread_version=3,
        )
        fork_request = RunAgentInput.model_validate(
            {
                "threadId": "external-thread-1",
                "runId": "external-run-fork",
                "parentRunId": "external-run-1",
                "state": {},
                "messages": [
                    {"id": "message-external-run-1", "role": "user", "content": "hello"},
                    {"id": "message-external-run-fork", "role": "user", "content": "alternate"},
                ],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            }
        )

        forked = await service.accept(
            actor=_actor(),
            agent_id=AGENT_ID,
            request=fork_request,
            last_event_id=None,
        )

    async with short_session(lifecycle_interaction_sessions) as database:
        forked_run = await database.scalar(select(RunRecord).where(RunRecord.id == forked.binding.run_id))
        second_run = await database.scalar(select(RunRecord).where(RunRecord.id == second.binding.run_id))
        thread_binding = await database.scalar(select(AguiThreadBindingRecord))
    assert forked_run is not None and second_run is not None and thread_binding is not None
    assert forked_run.parent_run_id == first.binding.run_id
    assert forked_run.lineage_kind == "fork"
    assert forked_run.thread_id != second_run.thread_id
    assert thread_binding.active_thread_id == forked_run.thread_id


async def test_hosted_stream_projects_standard_events_and_resumes_with_hosted_cursor(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    async with AsyncExitStack() as stack:
        service, stream, _objects = await _service(lifecycle_interaction_sessions, tmp_path, stack)
        attachment = await service.accept(
            actor=_actor(),
            agent_id=AGENT_ID,
            request=_request(),
            last_event_id=None,
        )
        for sequence, (event_type, payload) in enumerate(
            (
                (
                    "agui.text_message_start",
                    {"messageId": "message-output", "role": "assistant", "item_kind": "text_message"},
                ),
                ("agui.text_message_content", {"messageId": "message-output", "delta": "hello"}),
                ("agui.text_message_end", {"messageId": "message-output"}),
            ),
            start=1,
        ):
            await stream.append(
                attachment.binding.organization_id,
                RunStreamEvent(
                    event_id=deterministic_run_stream_event_id("hosted-test", str(sequence)),
                    event_type=event_type,
                    run_id=attachment.binding.run_id,
                    thread_id="thread_1234567890abcdef",
                    run_attempt_id="rat_1234567890abcdef",
                    harness_run_id="hrun_1234567890abcdef",
                    occurred_at=NOW + timedelta(seconds=sequence),
                    payload=payload,
                ),
            )
        await stream.close(
            attachment.binding.organization_id,
            attachment.binding.run_id,
            closed_at=NOW + timedelta(seconds=4),
        )

        frames = [frame async for frame in service.events(attachment)]
        cursor = frames[1].split(b"\n", maxsplit=1)[0].removeprefix(b"id: ").decode()
        resumed = await service.accept(
            actor=_actor(),
            agent_id=AGENT_ID,
            request=_request(),
            last_event_id=cursor,
        )
        resumed_frames = [frame async for frame in service.events(resumed)]

    assert len(frames) == 4
    assert b'"type":"RUN_STARTED"' in frames[0]
    assert b'"type":"TEXT_MESSAGE_START"' in frames[1]
    assert b"item_kind" not in frames[1]
    assert b'"type":"TEXT_MESSAGE_CONTENT"' in frames[2]
    assert b'"type":"TEXT_MESSAGE_END"' in frames[3]
    assert all(b"rat_1234567890abcdef" not in frame for frame in frames)
    assert resumed_frames == frames[2:]


async def test_hosted_cancel_resolves_binding_and_interrupts_foundation_run(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    async with AsyncExitStack() as stack:
        service, _stream, _objects = await _service(lifecycle_interaction_sessions, tmp_path, stack)
        attachment = await service.accept(
            actor=_actor(),
            agent_id=AGENT_ID,
            request=_request(),
            last_event_id=None,
        )
        request = HostedAguiCancelRequest(threadId="external-thread-1", runId="external-run-1")

        first = await service.cancel(actor=_actor(), agent_id=AGENT_ID, request=request)
        repeated = await service.cancel(actor=_actor(), agent_id=AGENT_ID, request=request)

    assert repeated == first
    assert first.status == "cancelled"
    async with short_session(lifecycle_interaction_sessions) as database:
        run = await database.scalar(select(RunRecord).where(RunRecord.id == attachment.binding.run_id))
    assert run is not None and run.status == "cancelled"


def _actor():
    from tests.gateway.test_commands import _actor as command_actor

    return command_actor()
