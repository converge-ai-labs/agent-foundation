"""A live client mount crosses real Worker, Control, Redis and native envd boundaries."""

import json
from unittest.mock import Mock

import pytest
from a13n_harness import AgentContext
from a13n_service.agents.reconstruction import AgentReconstructor
from a13n_service.environments.mount_domain import AddEnvironmentMountRequest
from a13n_service.environments.mount_models import RunEnvironmentMountRecord
from a13n_service.environments.mounts import RunEnvironmentMountService
from a13n_service.environments.websocket.admission import OnlineAdmission
from a13n_service.interactions.models import RunRecord
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.settings import Settings
from a13n_service.storage import short_session
from anyio import create_task_group, fail_after, sleep
from pydantic_ai import RunContext, Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from tests.environments.websocket.conftest import envd_binary as envd_binary
from tests.environments.websocket.conftest import relay_redis as relay_redis
from tests.hooks.support import hook_actor

from .test_attempt_execution import _accept_root
from .test_client_environment_runtime import native_client as native_client
from .test_websocket_use_authorization import client_environment as client_environment
from .worker_helpers import worker_runtime

pytestmark = pytest.mark.anyio


async def test_live_client_mount_is_usable_by_the_next_model_request(
    native_client,
    client_environment,
    interaction_sessions,
    interaction_object_store,
    relay_redis,
    redis_url,
    tmp_path,
    monkeypatch,
):
    connection_service, target, workspace, _ = native_client
    environment_service, _, _ = client_environment
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    mounts = RunEnvironmentMountService(
        interaction_sessions, OnlineAdmission(interaction_sessions, connection_service.coordination)
    )
    facades = []

    async def attach(ctx: RunContext[AgentContext]) -> str:
        facades.append(ctx.deps.environment)
        assert not ctx.deps.environment.snapshot.mounts
        await mounts.add(
            actor=hook_actor(),
            run_id=run.id,
            idempotency_key="client-during-tool",
            request=AddEnvironmentMountRequest(name="computer", environment_id=target.environment_id),
        )
        # Acceptance cannot change the Environment in the middle of this tool iteration.
        assert not ctx.deps.environment.snapshot.mounts
        return "accepted"

    provided = AgentReconstructor._provided_capabilities
    monkeypatch.setattr(
        AgentReconstructor,
        "_provided_capabilities",
        lambda self, node: (*provided(self, node), Capability(id="test.attach", tools=[Tool(attach)])),
    )
    requests = []

    async def model(messages, info):
        names = {tool.name for tool in info.function_tools}
        requests.append(names)
        if len(requests) == 1:
            assert "write" not in names
            yield {0: DeltaToolCall(name="attach", json_args="{}", tool_call_id="accept-client")}
        elif len(requests) == 2:
            assert [mount.name for mount in facades[0].snapshot.mounts] == ["computer"]
            assert facades[0].snapshot.default_mount == "computer"
            assert "write" in names
            assert "/environment/computer" in str(messages)
            yield {
                0: DeltaToolCall(
                    name="write",
                    json_args=json.dumps(
                        {"file_path": "/environment/computer/from-harness.txt", "content": "live client"}
                    ),
                    tool_call_id="write-client",
                )
            }
        else:
            assert (workspace / "from-harness.txt").read_text() == "live client"
            yield "completed"

    factory = Mock(spec=NativeModelFactory)
    factory.build.return_value = FunctionModel(stream_function=model)
    settings = Settings(
        service={"build_version": "test"},
        worker={"concurrency": 1, "poll_interval_seconds": 0.02},
        redis={"backend": "redis", "url": redis_url},
    )
    async with worker_runtime(
        interaction_sessions,
        interaction_object_store,
        tmp_path,
        monkeypatch,
        settings=settings,
        model_factory=factory,
        environment_catalog=environment_service.catalog,
        redis=relay_redis,
    ) as (worker, _):
        loop = worker.execution_loop
        connections = worker.client_connections
        assert loop is not None and connections is not None
        with fail_after(20):
            async with create_task_group() as tasks:
                tasks.start_soon(connections.run)
                tasks.start_soon(loop.run)
                try:
                    while True:
                        async with short_session(interaction_sessions) as session:
                            row = await session.get(RunRecord, run.id)
                            if row.status in {"completed", "failed"}:
                                assert row.status == "completed", row.failure_json
                                break
                        await sleep(0.02)
                finally:
                    await loop.drain()
                    await loop.wait_stopped()
                    await connections.close()
    assert len(requests) == 3
    async with short_session(interaction_sessions) as session:
        row = await session.get(RunRecord, run.id)
        mount = await session.get(RunEnvironmentMountRecord, (run.id, "computer"))
        assert row.environment_id is None
        assert row.environment_use_started_at is None
        assert mount.application_status == "ready"
        assert mount.applied_attempt_id is not None
        assert mount.use_started_at is not None
