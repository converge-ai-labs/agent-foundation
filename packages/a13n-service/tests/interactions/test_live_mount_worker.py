"""A real Worker installs accepted mounts between complete root tool iterations."""

import json
from unittest.mock import Mock

import pytest
from a13n_harness import AgentContext
from a13n_service.agents.models import AgentRecord
from a13n_service.agents.reconstruction import AgentReconstructor
from a13n_service.digests import digest_request
from a13n_service.environments.domain import CreateManagedEnvironmentRequest
from a13n_service.environments.local_directory import ManagedLocalDirectory
from a13n_service.environments.mount_models import RunEnvironmentMountRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.run_control import RunAttemptControl
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from anyio import create_task_group, fail_after, sleep
from pydantic_ai import RunContext, Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from tests.hooks.support import hook_actor

from .conftest import AGENT_ID, WORKSPACE_ID, effective_agent_config
from .mount_helpers import accepted_mount
from .test_attempt_execution import _accept_root
from .test_environment_runtime import template_config
from .worker_helpers import worker_runtime

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("files_enabled", [True, False])
async def test_live_mount_refreshes_facade_tools_and_context_at_next_root_request(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, files_enabled
):
    sessions = interaction_sessions
    service, template, lifecycle = await template_config(sessions, tmp_path, "on_use")
    environment = await service.create_environment(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="live-target",
        request=CreateManagedEnvironmentRequest(template_id=template.id),
    )
    async with transaction(sessions) as session:
        agent = await session.get(AgentRecord, AGENT_ID)
        agent.default_environment_template_id = None
    config = effective_agent_config()
    config = config.model_copy(
        update={
            "toolsets": {
                **config.toolsets,
                "files": config.toolsets["files"].model_copy(update={"enabled": files_enabled}),
            }
        }
    )
    config = config.model_copy(
        update={
            "content_digest": digest_request(config.model_dump(mode="json", by_alias=True, exclude={"content_digest"}))
        }
    )
    _, run, _ = await _accept_root(sessions, interaction_object_store, config=config)
    controllers = []
    facades = []
    bind = RunAttemptControl.bind_environment_mounts

    async def bind_mounts(control, mounts):
        controllers.append(control)
        await bind(control, mounts)

    monkeypatch.setattr(RunAttemptControl, "bind_environment_mounts", bind_mounts)

    async def attach(ctx: RunContext[AgentContext]) -> str:
        facades.append(ctx.deps.environment)
        assert not ctx.deps.environment.snapshot.mounts
        async with transaction(sessions) as session:
            session.add(accepted_mount(run.id, environment.id, name="computer", access="read_write"))
        # The same read path used by the watcher cannot mutate an active tool batch.
        await controllers[0].reconcile()
        assert not ctx.deps.environment.snapshot.mounts
        return "accepted"

    provided = AgentReconstructor._provided_capabilities
    monkeypatch.setattr(
        AgentReconstructor,
        "_provided_capabilities",
        lambda self, node: (
            *provided(self, node),
            Capability(id="test.attach", tools=[Tool(attach)]),
        ),
    )
    requests = []

    async def model(messages, info):
        names = {tool.name for tool in info.function_tools}
        requests.append(names)
        if len(requests) == 1:
            assert "view" not in names and "write" not in names
            yield {0: DeltaToolCall(name="attach", json_args="{}", tool_call_id="attach-client")}
        else:
            assert [mount.name for mount in facades[0].snapshot.mounts] == ["computer"]
            assert ("view" in names) is files_enabled
            assert ("write" in names) is files_enabled
            assert "/environment/computer" in str(messages)
            if len(requests) == 2 and files_enabled:
                yield {
                    0: DeltaToolCall(
                        name="write",
                        json_args=json.dumps({"file_path": "/environment/computer/probe.txt", "content": "installed"}),
                        tool_call_id="write-client-file",
                    )
                }
            else:
                yield "completed"

    factory = Mock(spec=NativeModelFactory)
    factory.build.return_value = FunctionModel(stream_function=model)
    async with worker_runtime(
        sessions,
        interaction_object_store,
        tmp_path,
        monkeypatch,
        settings=Settings(service={"build_version": "test"}, worker={"concurrency": 1, "poll_interval_seconds": 0.02}),
        model_factory=factory,
        environment_catalog=lifecycle.catalog,
    ) as (worker, _):
        loop = worker.execution_loop
        assert loop is not None
        with fail_after(15):
            async with create_task_group() as tasks:
                tasks.start_soon(loop.run)
                while True:
                    async with short_session(sessions) as session:
                        row = await session.get(RunRecord, run.id)
                        if row.status in {"completed", "failed"}:
                            assert row.status == "completed", row.failure_json
                            break
                    await sleep(0.02)
                await loop.drain()
                await loop.wait_stopped()
    assert len(requests) == (3 if files_enabled else 2)
    async with short_session(sessions) as session:
        mount = await session.get(RunEnvironmentMountRecord, (run.id, "computer"))
        assert mount.application_status == "ready"
        assert mount.applied_attempt_id == controllers[0].current_context.run_attempt_id
        assert mount.use_started_at is not None
    if files_enabled:
        assert (ManagedLocalDirectory(tmp_path, environment.id).path / "probe.txt").read_text() == "installed"
