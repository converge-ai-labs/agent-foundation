from __future__ import annotations

import pytest
from a2a.types import a2a_pb2 as a2a
from a13n_service.agents.models import AgentRevisionRecord
from a13n_service.gateway.a2a import A2AError, A2AService
from a13n_service.gateway.models import A2AContextBindingRecord, A2AMessageBindingRecord, A2ATaskBindingRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import short_session, transaction
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.agents.conftest import agent_config
from tests.gateway.test_commands import _actor, _commands, _complete_run, _Freezing, _frozen, _Preparation
from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import AGENT_ID, NOW

pytestmark = pytest.mark.anyio


def _request(
    *,
    message_id: str = "message-1",
    text: str = "hello",
    context_id: str = "",
    task_id: str = "",
) -> a2a.SendMessageRequest:
    return a2a.SendMessageRequest(
        message=a2a.Message(
            message_id=message_id,
            context_id=context_id,
            task_id=task_id,
            role=a2a.ROLE_USER,
            parts=[a2a.Part(text=text)],
        ),
        configuration=a2a.SendMessageConfiguration(return_immediately=True),
    )


async def _service(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> tuple[A2AService, LocalObjectStore]:
    objects = await LocalObjectStore.create(tmp_path / "a2a-objects")
    commands = _commands(sessions, objects, _Preparation(), _Freezing([_frozen()]))
    return (
        A2AService(
            sessions,
            commands,
            poll_interval_seconds=0.001,
            maximum_wait_seconds=0.02,
            clock=lambda: NOW,
        ),
        objects,
    )


async def test_initial_message_atomically_binds_task_and_replays(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    request = _request()

    first = await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)
    repeated = await service.send(actor=_actor(), agent_id=AGENT_ID, request=request)

    assert repeated == first
    assert first.status.state == a2a.TASK_STATE_SUBMITTED
    assert first.context_id.startswith("a2actx_")
    assert first.history[0].message_id == "message-1"
    async with short_session(lifecycle_interaction_sessions) as database:
        context = await database.scalar(select(A2AContextBindingRecord))
        task = await database.scalar(select(A2ATaskBindingRecord))
        message = await database.scalar(select(A2AMessageBindingRecord))
        run = await database.get(RunRecord, task.current_run_id) if task else None
    assert context is not None
    assert task is not None
    assert message is not None
    assert run is not None
    assert task.run_ids_json == [run.id]
    assert message.run_id == run.id


async def test_message_id_reuse_with_changed_content_conflicts(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request())

    with pytest.raises(A2AError) as captured:
        await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request(text="different"))

    assert captured.value.code == "message_id_conflict"


async def test_completed_task_projects_artifact_and_context_accepts_next_task(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, objects = await _service(lifecycle_interaction_sessions, tmp_path)
    first = await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request())
    async with short_session(lifecycle_interaction_sessions) as database:
        binding = await database.get(A2ATaskBindingRecord, first.id)
    assert binding is not None
    await _complete_run(lifecycle_interaction_sessions, objects, run_id=binding.current_run_id)

    completed = await service.get_task(actor=_actor(), agent_id=AGENT_ID, task_id=first.id)
    second = await service.send(
        actor=_actor(),
        agent_id=AGENT_ID,
        request=_request(message_id="message-2", text="again", context_id=first.context_id),
    )

    assert completed.status.state == a2a.TASK_STATE_COMPLETED
    assert completed.artifacts[0].parts[0].WhichOneof("content") == "data"
    assert second.id != first.id
    assert second.context_id == first.context_id
    assert second.status.state == a2a.TASK_STATE_SUBMITTED


async def test_cancel_task_uses_durable_interrupt(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    task = await service.send(actor=_actor(), agent_id=AGENT_ID, request=_request())

    cancelled = await service.cancel_task(actor=_actor(), agent_id=AGENT_ID, task_id=task.id)

    assert cancelled.status.state == a2a.TASK_STATE_CANCELED


async def test_public_card_projects_current_agent_protocol(
    lifecycle_interaction_sessions: async_sessionmaker[AsyncSession],
    tmp_path,
) -> None:
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    service, _objects = await _service(lifecycle_interaction_sessions, tmp_path)
    async with transaction(lifecycle_interaction_sessions) as database:
        revision = await database.get(AgentRevisionRecord, _frozen().agent_revision_id)
        assert revision is not None
        revision.config = agent_config().model_dump(mode="json")

    card = await service.public_agent_card(agent_id=AGENT_ID, base_url="https://foundation.example")

    assert card.name
    assert card.supported_interfaces[0].url == f"https://foundation.example/a2a/v1/agents/{AGENT_ID}"
    assert card.supported_interfaces[0].protocol_version == "1.0"
    assert card.capabilities.streaming
    assert card.capabilities.push_notifications
