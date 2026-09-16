"""Exercise actual admission, Worker composition, Harness tools and successor binding."""

import json
from unittest.mock import Mock

import pytest
from a13n_service.agent_configuration.definition import CONFIGURATION_TOOLS, READ_TOOLS
from a13n_service.agent_configuration.inputs import ConfigurationInputRequest
from a13n_service.agent_configuration.requests import CreateConfigurationThreadRequest
from a13n_service.agents.resolution import AgentResolver
from a13n_service.etags import resource_etag
from a13n_service.interactions.models import RunRecord
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.runtime import AcceptedModelSelector
from a13n_service.settings import Settings
from a13n_service.storage import short_session
from anyio import create_task_group, fail_after, sleep
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from ..agents.conftest import actor, agent_config
from ..interactions.worker_helpers import worker_runtime
from .test_drafts import apply_request, new_draft, services
from .test_inputs import inputs_service

pytestmark = pytest.mark.anyio


async def test_real_worker_edits_draft_and_post_apply_input_uses_successor(agent_sessions, tmp_path, monkeypatch):
    conversations, drafts, application = services(agent_sessions)
    draft = await new_draft(conversations)
    inputs, objects = await inputs_service(agent_sessions, tmp_path)
    request = ConfigurationInputRequest.model_validate(
        {
            "expected_thread_version": 1,
            "input": {"schema_version": "2", "content": [{"type": "text", "text": "Build a support agent"}]},
        }
    )
    first = await inputs.submit(
        actor=actor(), thread_id=draft.thread_id, request=request, idempotency_key="worker-first"
    )
    requests = []

    async def respond(messages, info):
        assert {tool.name for tool in info.function_tools} == CONFIGURATION_TOOLS | READ_TOOLS
        requests.append(messages)
        if len(requests) == 1:
            assert draft.id in repr(messages)
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args=json.dumps({"file_path": "/environment/builtin-skills/configure-agent/SKILL.md"}),
                    tool_call_id="read-knowledge",
                )
            }
        elif len(requests) == 2:
            results = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            knowledge = next(part.content for part in results if part.tool_name == "view")
            assert "configure-agent" in str(knowledge)
            yield {0: DeltaToolCall(name="get_configuration_draft", json_args="{}", tool_call_id="read-draft")}
        elif len(requests) == 3:
            results = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            result = next(part.content for part in results if part.tool_name == "get_configuration_draft")
            if isinstance(result, str):
                result = json.loads(result)
            assert result["draft_id"] == draft.id and result["status"] == "open"
            yield {
                0: DeltaToolCall(
                    name="update_configuration_draft",
                    json_args=json.dumps(
                        {
                            "update": {
                                "expected_version": result["version"],
                                "content_digest": result["content_digest"],
                                "operations": [
                                    {"op": "set", "path": [], "value": agent_config().model_dump(mode="json")}
                                ],
                                "creation_metadata": {"name": "Support"},
                            }
                        }
                    ),
                    tool_call_id="save-draft",
                )
            }
        else:
            yield "The configuration draft is saved for your review."

    model_factory = Mock(spec=NativeModelFactory)
    model_factory.build.return_value = FunctionModel(stream_function=respond)
    resolver = AgentResolver(agent_sessions, AcceptedModelSelector(agent_sessions, built_in_provider_registry()))
    async with worker_runtime(
        agent_sessions,
        objects,
        tmp_path,
        monkeypatch,
        settings=Settings(worker={"concurrency": 1, "poll_interval_seconds": 0.01}),
        model_factory=model_factory,
        configuration_resolver=resolver,
    ) as (runtime, _shared):
        loop = runtime.execution_loop
        with fail_after(15):
            async with create_task_group() as tasks:
                tasks.start_soon(loop.run)
                while True:
                    async with short_session(agent_sessions) as session:
                        run = await session.get(RunRecord, first.run_id)
                        assert run.status != "failed", run.failure_json
                        if run.status == "completed":
                            break
                    await sleep(0.02)
                await loop.drain()
                await loop.wait_stopped()
    saved = await drafts.get(actor=actor(), draft_id=draft.id)
    assert saved.version == 2 and saved.creation_metadata.name == "Support"
    receipt = await application.apply(
        actor=actor(),
        draft_id=saved.id,
        request=apply_request(saved),
        idempotency_key="apply-worker-draft",
        if_match=resource_etag(saved.id, saved.updated_at),
    )
    thread = await conversations.get_thread(actor=actor(), thread_id=draft.thread_id)
    second = await inputs.submit(
        actor=actor(),
        thread_id=draft.thread_id,
        request=request.model_copy(update={"expected_thread_version": thread.thread.version}),
        idempotency_key="worker-second",
    )
    async with short_session(agent_sessions) as session:
        original = (await session.get(RunRecord, first.run_id)).to_resource()
        successor = (await session.get(RunRecord, second.run_id)).to_resource()
    assert original.configuration_context.draft_id == draft.id
    assert successor.configuration_context.draft_id != draft.id
    assert successor.configuration_context.previous_application_receipt == receipt
    assert successor.configuration_context.mode == "update"
    assert successor.configuration_context.target_agent_id == receipt.agent_id
    assert successor.configuration_context.source_agent_revision_id == receipt.agent_revision_id
    # A second editing approach shares the existing Session identity and has its own draft.
    branch = await conversations.create_thread(
        actor=actor(),
        session_id=draft.session_id,
        request=CreateConfigurationThreadRequest(fork_from_run_id=first.run_id),
        idempotency_key="approach-two",
    )
    fork = await inputs.submit(
        actor=actor(),
        thread_id=branch.thread.id,
        request=request.model_copy(update={"expected_thread_version": branch.thread.version}),
        idempotency_key="fork-input",
    )
    async with short_session(agent_sessions) as session:
        forked = (await session.get(RunRecord, fork.run_id)).to_resource()
    assert forked.parent_run_id == first.run_id
    assert forked.configuration_context.draft_id == branch.latest_draft.id
