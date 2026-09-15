from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from a13n_service.agents.domain import AgentConfig
from a13n_service.agents.models import AgentRevisionRecord
from a13n_service.digests import digest_request
from a13n_service.gateway.agui_replay import HostedAguiReplayStore, hosted_agui_replay_key
from a13n_service.gateway.hosted_agui import (
    HostedAguiCancelRequest,
    HostedAguiError,
    HostedAguiService,
    HostedAguiTerminalProjector,
)
from a13n_service.gateway.models import AguiRunBindingRecord, AguiThreadBindingRecord
from a13n_service.http_errors import application_error_status
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.objects import RunStateStore
from a13n_service.lifecycle import LifecycleEventRecord
from a13n_service.run_stream import (
    RedisRunStream,
    RunReplayStore,
    RunStreamEvent,
    RunStreamReplayGap,
    deterministic_run_stream_event_id,
)
from a13n_service.storage import ObjectNotFound, short_session, transaction
from a13n_service.storage.config import RedisMemoryConfig
from a13n_service.storage.object_store import LocalObjectStore
from a13n_service.storage.redis import open_redis
from ag_ui.core import RunAgentInput
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.gateway.test_commands import _commands, _complete_run, _frozen, _Preparation, _wait_run
from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import AGENT_ID, NOW, agent_config
from tests.run_stream.support import activate_stream

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
    async with short_session(sessions) as database:
        revision = await database.get(AgentRevisionRecord, "agtr_1234567890abcdef")
        protocol = AgentConfig.model_validate(revision.config).protocol
    return (
        HostedAguiService(
            sessions,
            _commands(sessions, objects, _Preparation(), _frozen_resolver(protocol)),
            stream,
            RunReplayStore(objects),
            HostedAguiReplayStore(objects, max_events=1024, max_bytes=16 * 1024 * 1024),
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


async def _set_protocol_surface(
    sessions: async_sessionmaker[AsyncSession],
    *,
    state_schema: dict[str, Any] | None = None,
    context_schema: dict[str, Any] | None = None,
    required_tool: bool = False,
) -> None:
    base = agent_config()
    client_tools = (
        {
            "name": "lookup_order",
            "description": "Look up one order.",
            "parameters_json_schema": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    )
    payload = base.model_dump(mode="json", by_alias=True)
    payload["client_tools"] = list(client_tools)
    payload["protocol"].update(
        {
            "state_schema": state_schema,
            "context_schema": context_schema,
            "client_tools": [{"name": "lookup_order", "required": required_tool}],
        }
    )
    configured = base.__class__.model_validate(payload)
    async with transaction(sessions) as database:
        revision = await database.get(AgentRevisionRecord, "agtr_1234567890abcdef")
        assert revision is not None
        revision.config = configured.model_dump(mode="json", by_alias=True)


def _surface_request(*, state: object, context: list[dict[str, str]], tools: list[dict[str, object]]) -> RunAgentInput:
    return RunAgentInput.model_validate(
        {
            "threadId": "external-thread-1",
            "runId": "external-run-1",
            "state": state,
            "messages": [{"id": "message-external-run-1", "role": "user", "content": "hello"}],
            "tools": tools,
            "context": context,
            "forwardedProps": {},
        }
    )


def _frozen_resolver(protocol=None):
    from tests.gateway.test_commands import _Freezing

    frozen = _frozen()
    if protocol is not None:
        config = frozen.effective_config.model_copy(update={"protocol": protocol})
        config = config.model_copy(
            update={
                "content_digest": digest_request(
                    config.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
                )
            }
        )
        frozen = replace(frozen, effective_config=config)
    return _Freezing([frozen])


async def test_protocol_state_context_and_client_tools_are_validated_and_frozen(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    await _set_protocol_surface(
        lifecycle_interaction_sessions,
        state_schema={
            "type": "object",
            "properties": {"locale": {"type": "string"}},
            "required": ["locale"],
            "additionalProperties": False,
        },
        context_schema={
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"description": {"type": "string"}, "value": {"type": "string"}},
                "required": ["description", "value"],
                "additionalProperties": False,
            },
        },
        required_tool=True,
    )
    request = _surface_request(
        state={"locale": "zh-CN"},
        context=[{"description": "Customer tier", "value": "enterprise"}],
        tools=[
            {
                "name": "lookup_order",
                "description": "Look up one order.",
                "parameters": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                    "additionalProperties": False,
                },
            }
        ],
    )

    async with AsyncExitStack() as stack:
        service, _stream, _objects = await _service(lifecycle_interaction_sessions, tmp_path, stack)
        captured = []
        original = service._commands.runs.start

        async def capture_start(**kwargs):
            captured.append(kwargs["request"])
            return await original(**kwargs)

        service._commands.runs.start = capture_start  # type: ignore[method-assign]
        attachment = await service.accept(actor=_actor(), agent_id=AGENT_ID, request=request, last_event_id=None)

    assert attachment.binding.agent_revision_id == "agtr_1234567890abcdef"
    assert len(captured) == 1
    override = captured[0].config_override
    assert override is not None
    assert [tool.name for tool in override.client_tools or ()] == ["lookup_order"]
    assert captured[0].protocol_context.state == {"locale": "zh-CN"}
    assert captured[0].protocol_context.context[0].value == "enterprise"
    assert "state" not in captured[0].input.model_dump()
    async with short_session(lifecycle_interaction_sessions) as database:
        run = (await database.get(RunRecord, attachment.binding.run_id)).to_resource()
    frozen_state = await RunStateStore(_objects).read_run(run)
    assert frozen_state.envelope.protocol_context == captured[0].protocol_context


@pytest.mark.parametrize(
    ("state", "context", "code"),
    (
        ({"locale": 1}, [], "agui_state_invalid"),
        ({}, [{"description": "Customer tier", "value": "enterprise"}], "agui_context_not_allowed"),
    ),
)
async def test_protocol_state_and_context_reject_values_outside_revision_policy(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
    state: object,
    context: list[dict[str, str]],
    code: str,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    await _set_protocol_surface(
        lifecycle_interaction_sessions,
        state_schema={
            "type": "object",
            "properties": {"locale": {"type": "string"}},
            "required": ["locale"],
        },
    )
    request = _surface_request(state=state, context=context, tools=[])

    async with AsyncExitStack() as stack:
        service, _stream, _objects = await _service(lifecycle_interaction_sessions, tmp_path, stack)
        with pytest.raises(HostedAguiError) as captured:
            await service.accept(actor=_actor(), agent_id=AGENT_ID, request=request, last_event_id=None)

    assert captured.value.code == code


async def test_waiting_run_cannot_change_optional_client_tool_surface(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    await _set_protocol_surface(lifecycle_interaction_sessions)
    first_request = _surface_request(
        state={},
        context=[],
        tools=[
            {
                "name": "lookup_order",
                "description": "Look up one order.",
                "parameters": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                    "additionalProperties": False,
                },
            }
        ],
    )

    async with AsyncExitStack() as stack:
        service, _stream, objects = await _service(lifecycle_interaction_sessions, tmp_path, stack)
        first = await service.accept(actor=_actor(), agent_id=AGENT_ID, request=first_request, last_event_id=None)
        await _wait_run(lifecycle_interaction_sessions, objects, run_id=first.binding.run_id)
        changed = RunAgentInput.model_validate(
            {
                "threadId": "external-thread-1",
                "runId": "external-run-2",
                "parentRunId": "external-run-1",
                "state": {},
                "messages": [
                    {"id": "message-external-run-1", "role": "user", "content": "hello"},
                    {"id": "message-external-run-2", "role": "user", "content": "continue"},
                ],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            }
        )

        with pytest.raises(HostedAguiError) as captured:
            await service.accept(actor=_actor(), agent_id=AGENT_ID, request=changed, last_event_id=None)

    assert captured.value.code == "agui_tool_surface_changed"


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
    assert application_error_status(captured.value) == 409


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
        frames = [frame async for frame in service.events(first)]
        assert b'"name":"a13n.service.run_status"' in frames[-1]
        assert b'"status":"waiting"' in frames[-1]

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


async def test_tool_message_tail_maps_to_exact_waiting_client_tool_feedback(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    await _set_protocol_surface(lifecycle_interaction_sessions, required_tool=True)
    tools = [
        {
            "name": "lookup_order",
            "description": "Look up one order.",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        }
    ]
    async with AsyncExitStack() as stack:
        service, _stream, objects = await _service(lifecycle_interaction_sessions, tmp_path, stack)
        first = await service.accept(
            actor=_actor(),
            agent_id=AGENT_ID,
            request=_surface_request(state={}, context=[], tools=tools),
            last_event_id=None,
        )
        await _wait_run(
            lifecycle_interaction_sessions,
            objects,
            run_id=first.binding.run_id,
            pending_kind="client_tool",
        )
        request = RunAgentInput.model_validate(
            {
                "threadId": "external-thread-1",
                "runId": "external-run-tool-result",
                "parentRunId": "external-run-1",
                "state": {},
                "messages": [
                    {"id": "message-external-run-1", "role": "user", "content": "hello"},
                    {
                        "id": "tool-result-1",
                        "role": "tool",
                        "toolCallId": "client-tool-1",
                        "content": '{"status":"shipped"}',
                    },
                ],
                "tools": tools,
                "context": [],
                "forwardedProps": {},
            }
        )

        feedback = await service.accept(
            actor=_actor(),
            agent_id=AGENT_ID,
            request=request,
            last_event_id=None,
        )

    async with short_session(lifecycle_interaction_sessions) as database:
        successor = await database.get(RunRecord, feedback.binding.run_id)
    assert successor is not None
    assert successor.parent_run_id == first.binding.run_id
    assert successor.input_kind == "waiting_feedback"
    assert successor.input_json["resolutions"] == [
        {
            "call_id": "client-tool-1",
            "kind": "client_tool",
            "outcome": "complete",
            "result": '{"status":"shipped"}',
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
        await activate_stream(
            stream, attachment.binding.organization_id, attachment.binding.run_id, "thread_1234567890abcdef"
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
                attempt_number=1,
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


async def test_sealed_hosted_replay_survives_native_stream_loss_and_bounds_cursor(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    async with AsyncExitStack() as stack:
        service, stream, objects = await _service(lifecycle_interaction_sessions, tmp_path, stack)
        attachment = await service.accept(
            actor=_actor(),
            agent_id=AGENT_ID,
            request=_request(),
            last_event_id=None,
        )
        await activate_stream(
            stream, attachment.binding.organization_id, attachment.binding.run_id, "thread_1234567890abcdef"
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
                    event_id=deterministic_run_stream_event_id("hosted-retained-test", str(sequence)),
                    event_type=event_type,
                    run_id=attachment.binding.run_id,
                    thread_id="thread_1234567890abcdef",
                    run_attempt_id="rat_1234567890abcdef",
                    harness_run_id="hrun_1234567890abcdef",
                    occurred_at=NOW + timedelta(seconds=sequence),
                    payload=payload,
                ),
                attempt_number=1,
            )
        await _complete_run(lifecycle_interaction_sessions, objects, run_id=attachment.binding.run_id)
        await stream.close(
            attachment.binding.organization_id,
            attachment.binding.run_id,
            closed_at=NOW + timedelta(seconds=10),
        )

        async with short_session(lifecycle_interaction_sessions) as database:
            terminal = await database.scalar(
                select(LifecycleEventRecord).where(
                    LifecycleEventRecord.run_id == attachment.binding.run_id,
                    LifecycleEventRecord.event_type == "run.completed",
                )
            )
        assert terminal is not None
        source = await stream.complete_source(attachment.binding.organization_id, attachment.binding.run_id)
        await HostedAguiTerminalProjector(
            lifecycle_interaction_sessions,
            HostedAguiReplayStore(objects, max_events=1, max_bytes=16 * 1024 * 1024),
        ).project(terminal.to_resource(), source)
        with pytest.raises(ObjectNotFound):
            await objects.stat(
                hosted_agui_replay_key(
                    attachment.binding.organization_id,
                    attachment.binding.run_binding_id,
                )
            )
        await HostedAguiTerminalProjector(
            lifecycle_interaction_sessions,
            HostedAguiReplayStore(objects, max_events=1024, max_bytes=16 * 1024 * 1024),
        ).project(terminal.to_resource(), source)
        replay_info = await objects.stat(
            hosted_agui_replay_key(
                attachment.binding.organization_id,
                attachment.binding.run_binding_id,
            )
        )
        await stream._redis.flushall()  # type: ignore[attr-defined]
        frames = [frame async for frame in service.events(attachment)]
        cursor = frames[1].split(b"\n", maxsplit=1)[0].removeprefix(b"id: ").decode()
        resumed = await service.accept(
            actor=_actor(),
            agent_id=AGENT_ID,
            request=_request(),
            last_event_id=cursor,
        )
        resumed_frames = [frame async for frame in service.events(resumed)]
        last_cursor = frames[-1].split(b"\n", maxsplit=1)[0].removeprefix(b"id: ").decode()
        invalid_cursor = last_cursor.rsplit("_", maxsplit=1)[0] + "_999"
        with pytest.raises(HostedAguiError) as captured:
            await service.accept(
                actor=_actor(),
                agent_id=AGENT_ID,
                request=_request(),
                last_event_id=invalid_cursor,
            )

    assert replay_info.content_type == "application/zstd"
    assert len(frames) == 5
    assert b'"type":"RUN_FINISHED"' in frames[-1]
    assert resumed_frames == frames[2:]
    assert captured.value.code == "agui_cursor_invalid"
    assert application_error_status(captured.value) == 409


@pytest.mark.parametrize("publication_busy", [False, True])
async def test_sealed_reconnect_uses_intact_native_prefix_before_snapshot_publication(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession], tmp_path, monkeypatch, publication_busy
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    async with AsyncExitStack() as stack:
        service, stream, objects = await _service(lifecycle_interaction_sessions, tmp_path, stack)
        attachment = await service.accept(actor=_actor(), agent_id=AGENT_ID, request=_request(), last_event_id=None)
        await activate_stream(
            stream, attachment.binding.organization_id, attachment.binding.run_id, "thread_1234567890abcdef"
        )
        events = service.events(attachment)
        started = await anext(events)
        await events.aclose()
        cursor = started.split(b"\n", maxsplit=1)[0].removeprefix(b"id: ").decode()
        await _complete_run(lifecycle_interaction_sessions, objects, run_id=attachment.binding.run_id)
        # Spec 21/22: no history was lost. Durable sealing and stream snapshot
        # publication are separate boundaries, so this is not a replay gap.
        resumed = await service.accept(actor=_actor(), agent_id=AGENT_ID, request=_request(), last_event_id=cursor)
        await stream.close(attachment.binding.organization_id, attachment.binding.run_id, closed_at=NOW)
        if publication_busy:
            from a13n_service.storage import ObjectStoreUnavailable

            async def busy(*args, **kwargs):
                raise ObjectStoreUnavailable("Another publisher holds the lease")

            monkeypatch.setattr(objects, "put", busy)
        frames = [frame async for frame in service.events(resumed)]
    assert len(frames) == 1
    assert b'"type":"RUN_FINISHED"' in frames[0]


async def test_hosted_cancel_resolves_binding_and_interrupts_service_run(
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
        await activate_stream(
            stream, attachment.binding.organization_id, attachment.binding.run_id, "thread_1234567890abcdef"
        )
        request = HostedAguiCancelRequest(threadId="external-thread-1", runId="external-run-1")

        first = await service.cancel(actor=_actor(), agent_id=AGENT_ID, request=request)
        repeated = await service.cancel(actor=_actor(), agent_id=AGENT_ID, request=request)
        await stream.close(
            attachment.binding.organization_id,
            attachment.binding.run_id,
            closed_at=NOW + timedelta(seconds=1),
        )
        frames = [frame async for frame in service.events(attachment)]

    assert repeated == first
    assert first.status == "cancelled"
    assert b'"type":"RUN_STARTED"' in frames[0]
    assert b'"type":"RUN_ERROR"' in frames[-1]
    assert b'"code":"run_cancelled"' in frames[-1]
    assert all(b'"type":"RUN_FINISHED"' not in frame for frame in frames)
    async with short_session(lifecycle_interaction_sessions) as database:
        run = await database.scalar(select(RunRecord).where(RunRecord.id == attachment.binding.run_id))
    assert run is not None and run.status == "cancelled"


def _actor():
    from tests.gateway.test_commands import _actor as command_actor

    return command_actor()


async def test_hosted_replay_gap_uses_service_namespace(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
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
        monkeypatch.setattr(
            stream,
            "read",
            AsyncMock(side_effect=RunStreamReplayGap(retained_floor=None, high_watermark=None)),
        )
        frames = [frame async for frame in service.events(attachment)]

    assert len(frames) == 2
    assert b'"type":"RUN_STARTED"' in frames[0]
    assert b'"name":"a13n.service.replay_gap"' in frames[1]


@pytest.mark.parametrize("reason", ["lease_expired", "retry_after_failure", "planned_handoff", "pending_input"])
async def test_recovery_is_safe_ordered_and_identical_in_live_and_sealed_hosted_replay(
    lifecycle_interaction_sessions,
    tmp_path,
    reason,
):
    import json

    await seed_hook_actor_access(lifecycle_interaction_sessions)
    async with AsyncExitStack() as stack:
        service, stream, objects = await _service(lifecycle_interaction_sessions, tmp_path, stack)
        attachment = await service.accept(actor=_actor(), agent_id=AGENT_ID, request=_request(), last_event_id=None)
        org, run = attachment.binding.organization_id, attachment.binding.run_id
        await activate_stream(stream, org, run, "thread_1234567890abcdef")
        await activate_stream(
            stream,
            org,
            run,
            "thread_1234567890abcdef",
            attempt_id="rat_2222222222222222",
            number=2,
            reason=reason,
        )
        await stream.append(
            org,
            RunStreamEvent(
                event_id=deterministic_run_stream_event_id("hosted-recovery-test", "message"),
                event_type="agui.text_message_start",
                run_id=run,
                thread_id="thread_1234567890abcdef",
                run_attempt_id="rat_2222222222222222",
                occurred_at=NOW,
                payload={"messageId": "new-message", "role": "assistant"},
            ),
            attempt_number=2,
        )
        await stream.close(org, run, closed_at=NOW + timedelta(seconds=10))
        live = [frame async for frame in service.events(attachment)]
        assert len(live) == 3
        recovery = json.loads(live[1].split(b"data: ", 1)[1])
        assert recovery["type"] == "CUSTOM" and recovery["name"] == "a13n.service.run_recovery"
        assert recovery["value"] == {
            "schema_version": "1",
            "event_id": recovery["value"]["event_id"],
            "runId": "external-run-1",
            "reason": reason,
        }
        assert b'"type":"TEXT_MESSAGE_START"' in live[2]
        assert all(
            b"rat_" not in frame and b"attempt_number" not in frame and b"lease_token" not in frame for frame in live
        )
        recovery_cursor = live[1].split(b"\n", 1)[0].removeprefix(b"id: ").decode()
        resumed = await service.accept(
            actor=_actor(), agent_id=AGENT_ID, request=_request(), last_event_id=recovery_cursor
        )
        assert [frame async for frame in service.events(resumed)] == live[2:]
        await _complete_run(lifecycle_interaction_sessions, objects, run_id=run)
        async with short_session(lifecycle_interaction_sessions) as database:
            terminal = await database.scalar(
                select(LifecycleEventRecord).where(
                    LifecycleEventRecord.run_id == run, LifecycleEventRecord.event_type == "run.completed"
                )
            )
        source = await stream.complete_source(org, run)
        await HostedAguiTerminalProjector(
            lifecycle_interaction_sessions,
            HostedAguiReplayStore(objects, max_events=1024, max_bytes=16 * 1024 * 1024),
        ).project(terminal.to_resource(), source)
        await stream._redis.flushdb()
        retained = [frame async for frame in service.events(attachment)]
        for original, replayed in zip(live, retained[:3], strict=True):
            assert original.split(b"\n", 1)[0] == replayed.split(b"\n", 1)[0]
            assert json.loads(original.split(b"data: ", 1)[1]) == json.loads(replayed.split(b"data: ", 1)[1])
        assert b'"type":"RUN_FINISHED"' in retained[-1]
        resumed = await service.accept(
            actor=_actor(), agent_id=AGENT_ID, request=_request(), last_event_id=recovery_cursor
        )
        assert [frame async for frame in service.events(resumed)] == retained[2:]
