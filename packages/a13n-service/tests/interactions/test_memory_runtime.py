"""Memory uses accepted Attempt authority and stable Service subjects, not model IDs."""

import json

import httpx2
import pytest
from a13n_harness import AgentDefinition, AgentSpec, HarnessBuilder
from a13n_harness.capabilities.mem0 import Mem0Scope
from a13n_harness.capabilities.mem0_backends import Mem0OSSBackend
from a13n_harness.errors import RunError
from a13n_service.agents.models import AgentRecord
from a13n_service.interactions.models import RunAttemptRecord
from a13n_service.memory.domain import MemoryScope, MemorySelection
from a13n_service.memory.runtime import memory_capability
from a13n_service.memory.scopes import MemoryAuthorizer, memory_subject
from a13n_service.memory.service import MemoryService
from a13n_service.storage import transaction
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from tests.hooks.support import hook_actor
from tests.memory.test_api import native_transport

from .conftest import NOW, ORGANIZATION_ID, WORKSPACE_ID
from .worker_helpers import accepted_running_attempt

pytestmark = pytest.mark.anyio


async def test_auto_recall_tools_and_next_run_keep_service_thread_namespace(
    interaction_sessions, interaction_object_store, monkeypatch
):
    run, context = await accepted_running_attempt(interaction_sessions, interaction_object_store)
    monkeypatch.setattr("a13n_service.memory.runtime.utc_now", lambda: NOW)
    records, calls = {}, []
    async with httpx2.AsyncClient(
        base_url="http://oss/", transport=httpx2.MockTransport(native_transport(records, calls, httpx2.Response))
    ) as client:
        service = MemoryService(Mem0OSSBackend(client), MemoryAuthorizer(interaction_sessions))
        subject = memory_subject(ORGANIZATION_ID, WORKSPACE_ID, Mem0Scope.THREAD, run.thread_id)
        selection = MemoryScope(scope="thread", subject_id=run.thread_id)
        await service.add(
            actor=hook_actor(), workspace_id=WORKSPACE_ID, selection=selection, text="Remembered evidence"
        )
        capability = memory_capability(
            service,
            run=run,
            workspace_id=WORKSPACE_ID,
            agent_id=run.agent_id,
            selection=MemorySelection(scope="thread"),
            current_context=lambda: context,
        )
        assert capability.auto_recall is True
        step = 0

        async def model(messages, info):
            nonlocal step
            assert {tool.name for tool in info.function_tools} == {"memory_add", "memory_search", "memory_list"}
            if step == 0:
                assert "Remembered evidence" in repr(messages)
                step += 1
                yield {
                    0: DeltaToolCall(
                        name="memory_add", json_args=json.dumps({"text": "Added by tool"}), tool_call_id="write"
                    )
                }
            else:
                yield "done"

        harness = HarnessBuilder(configured_plugins_enabled=False).build(
            AgentDefinition(
                agent=AgentSpec(name="Memory"),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=(capability,),
            )
        )
        first = await harness.run("Recall evidence")
        assert first.output_or_raise() == "done"
        second = await harness.run("Recall again", previous_state=first.state)
        assert second.output_or_raise() == "done"
        assert len([call for call in calls if call[1] == "/search"]) == 2
        assert {record["memory"] for record in records.values()} == {"Remembered evidence", "Added by tool"}
        assert all(record["run_id"] == subject.value for record in records.values())
        assert all(subject.value not in str(message) for message in first.state.message_history)
        assert not client.is_closed
        async with transaction(interaction_sessions) as session:
            attempt = await session.get(RunAttemptRecord, context.run_attempt_id)
            attempt.lease_token_digest = "0" * 64
        before = len(calls)
        with pytest.raises(RunError, match="authority"):
            await capability.backend.list(subject, limit=5)
        assert len(calls) == before


async def test_child_agent_scope_is_independent_and_disabled_agent_fails_before_io(
    interaction_sessions, interaction_object_store, monkeypatch
):
    run, context = await accepted_running_attempt(interaction_sessions, interaction_object_store)
    monkeypatch.setattr("a13n_service.memory.runtime.utc_now", lambda: NOW)
    child_id = "ap_memorychild123456"
    async with transaction(interaction_sessions) as session:
        root = await session.get(AgentRecord, run.agent_id)
        fields = {column.name: getattr(root, column.name) for column in AgentRecord.__table__.columns}
        fields.update(id=child_id, key="memory-child", name="Memory child", current_revision_id="apr_memorychild123456")
        session.add(AgentRecord(**fields))
    records, calls = {}, []
    async with httpx2.AsyncClient(
        base_url="http://oss/", transport=httpx2.MockTransport(native_transport(records, calls, httpx2.Response))
    ) as client:
        service = MemoryService(Mem0OSSBackend(client), MemoryAuthorizer(interaction_sessions))
        root = memory_capability(
            service,
            run=run,
            workspace_id=WORKSPACE_ID,
            agent_id=run.agent_id,
            selection=MemorySelection(scope="agent"),
            current_context=lambda: context,
        )
        child = memory_capability(
            service,
            run=run,
            workspace_id=WORKSPACE_ID,
            agent_id=child_id,
            selection=MemorySelection(scope="agent"),
            current_context=lambda: context,
        )
        assert root.scope_ids[Mem0Scope.THREAD] == child.scope_ids[Mem0Scope.THREAD]
        assert root.scope_ids[Mem0Scope.AGENT] != child.scope_ids[Mem0Scope.AGENT]
        root_subject = memory_subject(ORGANIZATION_ID, WORKSPACE_ID, Mem0Scope.AGENT, run.agent_id)
        child_subject = memory_subject(ORGANIZATION_ID, WORKSPACE_ID, Mem0Scope.AGENT, child_id)
        # Workspace authority includes both Agents, but the binding never accepts a parent's subject as the child's.
        with pytest.raises(RunError, match="scope"):
            await child.backend.add("wrong owner", subject=root_subject)
        await child.backend.add("child memory", subject=child_subject)
        assert not (await root.backend.list(root_subject, limit=5))["results"]
        async with transaction(interaction_sessions) as session:
            child_record = await session.get(AgentRecord, child_id)
            child_record.enabled = False
        before = len(calls)
        with pytest.raises(RunError, match="authority"):
            await child.backend.list(child_subject, limit=5)
        assert len(calls) == before
