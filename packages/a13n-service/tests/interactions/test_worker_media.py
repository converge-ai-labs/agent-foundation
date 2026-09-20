"""Native-first media dispatch through real Service Worker and child execution."""

import json
import traceback
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness import AgentContext
from a13n_harness.spec import ModelCapability
from a13n_harness.toolsets.file_media import AgentMediaUnderstandingProvider
from a13n_service.agents.domain import ChildEnvironmentPolicy
from a13n_service.agents.reconstruction import AgentReconstructor
from a13n_service.environments.local_directory import ManagedLocalDirectory
from a13n_service.iam.models import RoleBindingRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.models.runtime import SnapshotRunModelResolver
from a13n_service.process.control.subagent import build_subagent_maintenance
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from anyio import create_task_group, fail_after, sleep
from pydantic_ai import BinaryContent, RunContext, Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from .test_attempt_execution import _accept_root
from .test_environment_runtime import template_config
from .test_subagent_acceptance import CHILD_REVISION_ID, _grant_and_seed_child
from .test_worker_subagents import CHILD_MODEL_ID, frozen_graph, rehash
from .worker_helpers import INTEGRATION_COMPLETION_SECONDS, INTEGRATION_LEASE_SECONDS, worker_runtime

pytestmark = [pytest.mark.anyio, pytest.mark.timeout(180)]
MEDIA_MODEL_ID = "mdl_media12345678901"


@pytest.mark.parametrize("mode", ["root", "inline", "async"])
@pytest.mark.parametrize("native", [False, True])
async def test_worker_view_media_uses_executing_thread_and_own_settings(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, mode, native
):
    failures = []
    original_failure = RunAttemptControl.fail_execution

    async def capture_failure(self, *args, **kwargs):
        failures.append(traceback.format_exc())
        return await original_failure(self, *args, **kwargs)

    monkeypatch.setattr(RunAttemptControl, "fail_execution", capture_failure)
    await _grant_and_seed_child(interaction_sessions)
    async with transaction(interaction_sessions) as session:
        (await session.get(RoleBindingRecord, "rbac_3333333333333333")).role_key = "admin"
    monkeypatch.setattr("tests.interactions.test_environment_runtime.seed_hook_actor_access", AsyncMock())
    _, _, lifecycle = await template_config(interaction_sessions, tmp_path, "on_run")
    config = frozen_graph("async" if mode == "async" else "inline")
    media = config.resolved_model.model_copy(
        update={
            "execution": config.resolved_model.execution.model_copy(
                update={
                    "model_id": MEDIA_MODEL_ID,
                    "model_key": "media",
                    "upstream_model": "media-upstream",
                }
            ),
            "settings": {"temperature": 0.7, "max_tokens": 111},
        }
    )

    def with_media(node):
        primary = node.resolved_model.model_copy(
            update={
                "characteristics": node.resolved_model.characteristics.model_copy(
                    update={
                        "capabilities": frozenset({ModelCapability.IMAGE_UNDERSTANDING}) if native else frozenset(),
                    }
                ),
            }
        )
        return rehash(node.model_copy(update={"resolved_model": primary, "media_understanding": {"image": media}}))

    child = config.child_configs[CHILD_REVISION_ID]
    config = with_media(config)
    config = rehash(
        config.model_copy(
            update={
                "resolved_subagents": ()
                if mode == "root"
                else tuple(
                    edge.model_copy(update={"environment": ChildEnvironmentPolicy(mode="shared")})
                    for edge in config.resolved_subagents
                ),
                "child_configs": {}
                if mode == "root"
                else {
                    CHILD_REVISION_ID: child.model_copy(
                        update={
                            "effective_config": with_media(
                                child.effective_config.model_copy(update={"connection_tools": ()})
                            ),
                            "connection_selections": (),
                        }
                    ),
                },
            }
        )
    )
    states, parent, _ = await _accept_root(interaction_sessions, interaction_object_store, config=config)
    async with short_session(interaction_sessions) as session:
        stored = await session.get(RunRecord, parent.id)
        directory = ManagedLocalDirectory(tmp_path, stored.environment_id).path
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sample.png").write_bytes(b"\x89PNG")
    primary_threads = []
    media_threads = []
    original_resolve = SnapshotRunModelResolver.resolve

    async def resolve(self, model_id, *, thread_id):
        (media_threads if model_id == MEDIA_MODEL_ID else primary_threads).append(thread_id)
        return await original_resolve(self, model_id, thread_id=thread_id)

    monkeypatch.setattr(SnapshotRunModelResolver, "resolve", resolve)
    ambient = Mock(side_effect=AssertionError("Configured or native image must not use environment fallback"))
    monkeypatch.setattr(AgentMediaUnderstandingProvider, "from_environment", ambient)
    executing_threads = []

    async def identify(ctx: RunContext[AgentContext]) -> str:
        executing_threads.append(ctx.deps.thread_id)
        return "identified"

    provided = AgentReconstructor._provided_capabilities
    monkeypatch.setattr(
        AgentReconstructor,
        "_provided_capabilities",
        lambda self, node: (
            *provided(self, node),
            Capability(id="test.identity", tools=[Tool(identify)]),
        ),
    )
    returned = []
    binaries = []
    finished = []

    async def primary(messages, info, *, child):
        tools = {
            part.tool_name
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        }
        if mode != "root" and not child:
            if "delegate" not in tools:
                yield {
                    0: DeltaToolCall(
                        name="delegate",
                        json_args=json.dumps(
                            {
                                "subagent_name" if mode == "async" else "subagent": "researcher",
                                "prompt": "Read image",
                            }
                        ),
                        tool_call_id="delegate-media",
                    )
                }
            else:
                yield "parent complete"
            return
        if "identify" not in tools:
            yield {0: DeltaToolCall(name="identify", json_args="{}", tool_call_id="identify")}
        elif "view" not in tools:
            yield {
                0: DeltaToolCall(
                    name="view",
                    json_args='{"file_path":"sample.png","instructions":"Read label"}',
                    tool_call_id="view-media",
                )
            }
        else:
            for message in messages:
                if isinstance(message, ModelRequest):
                    for part in message.parts:
                        if isinstance(part, ToolReturnPart) and part.tool_name == "view":
                            returned.append(part.content)
                        if isinstance(part, UserPromptPart) and isinstance(part.content, list):
                            binaries.extend(item for item in part.content if isinstance(item, BinaryContent))
            finished.append(True)
            yield "reader complete"

    async def auxiliary(messages, info):
        assert info.model_settings == {"temperature": 0.7, "max_tokens": 111}
        return ModelResponse(parts=[TextPart("label from media model")])

    async def build(snapshot, _provider):
        if snapshot.model_id == MEDIA_MODEL_ID:
            return FunctionModel(auxiliary)

        async def stream(messages, info):
            async for item in primary(messages, info, child=snapshot.model_id == CHILD_MODEL_ID):
                yield item

        return FunctionModel(stream_function=stream)

    factory = Mock(spec=NativeModelFactory)
    factory.build.side_effect = build
    settings = Settings(
        service={"build_version": "test"},
        worker={"concurrency": 1, "poll_interval_seconds": 0.02, "lease_seconds": INTEGRATION_LEASE_SECONDS},
        subagents={"reconcile_poll_interval_seconds": 0.02},
    )
    async with worker_runtime(
        interaction_sessions,
        interaction_object_store,
        tmp_path,
        monkeypatch,
        settings=settings,
        model_factory=factory,
        environment_catalog=lifecycle.catalog,
    ) as (runtime, shared):
        loop = runtime.execution_loop
        maintenance = build_subagent_maintenance(settings, shared, runtime.run_display)
        with fail_after(INTEGRATION_COMPLETION_SECONDS):
            async with create_task_group() as tasks:
                tasks.start_soon(loop.run)
                while not finished:
                    await maintenance.reconcile_once()
                    async with short_session(interaction_sessions) as session:
                        root = await session.get(RunRecord, parent.id)
                        assert root.status != "failed", "\n".join(failures)
                    await sleep(0.02)
                await loop.drain()
                await loop.wait_stopped()
                tasks.cancel_scope.cancel()
    assert executing_threads and executing_threads[0] in primary_threads
    assert (executing_threads[0] != primary_threads[0]) is (mode != "root")
    assert returned
    if native:
        assert media_threads == []
        assert binaries and binaries[0].data == b"\x89PNG"
    else:
        assert media_threads == [executing_threads[0]]
        assert returned == ["label from media model"]
    ambient.assert_not_called()
    retained = await states.read(parent.organization_id, parent.id)
    assert retained.envelope.effective_agent_config.media_understanding == {"image": media}
