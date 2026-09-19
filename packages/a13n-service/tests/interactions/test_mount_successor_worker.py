"""A retained successor reconstructs usable mounts from accepted database facts."""

import json
from unittest.mock import Mock

import pytest
from a13n_harness.providers.endpoint_policy import EndpointPolicy
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.environments.domain import CreateManagedEnvironmentRequest
from a13n_service.environments.local_directory import ManagedLocalDirectory
from a13n_service.environments.mount_models import RunEnvironmentMountRecord
from a13n_service.hooks import InlineHookValidator
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.initialization import RunStateSeed, initialize_retry_state
from a13n_service.interactions.models import RunRecord, ThreadRecord
from a13n_service.interactions.objects import RunPayloadStore
from a13n_service.models.model_factory import NativeModelFactory
from a13n_service.settings import Settings
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import utc_now
from anyio import create_task_group, fail_after, sleep
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from tests.hooks.support import hook_actor
from tests.lifecycle_support import test_lifecycle_writer
from tests.memory.selection_support import ordinary_memory

from .conftest import AGENT_ID, WORKSPACE_ID
from .mount_helpers import accepted_mount
from .test_attempt_execution import _accept_root
from .test_environment_retention import cancel
from .test_environment_runtime import template_config
from .worker_helpers import worker_runtime

pytestmark = pytest.mark.anyio


async def test_retry_worker_installs_inherited_mount_before_first_model_request(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch
):
    sessions, objects = interaction_sessions, interaction_object_store
    service, template, lifecycle = await template_config(sessions, tmp_path, "on_use")
    target = await service.create_environment(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="retry-target",
        request=CreateManagedEnvironmentRequest(template_id=template.id),
    )
    async with transaction(sessions) as database:
        agent = await database.get(AgentRecord, AGENT_ID)
        revision = await database.get(AgentRevisionRecord, agent.default_revision_id)
        revision.config = {**revision.config, "default_environment_template_id": None}
    states, source, initial = await _accept_root(sessions, objects)
    async with transaction(sessions) as database:
        database.add(accepted_mount(source.id, target.id, name="computer"))
    await cancel(sessions, objects, source, utc_now())
    seed = RunStateSeed(
        run_id="run_7777777777777777",
        agent_id=source.agent_id,
        agent_revision_id=source.agent_revision_id,
        effective_agent_config=initial.effective_agent_config,
    )
    successor = source.model_copy(
        update={
            "id": seed.run_id,
            "retry_of_run_id": source.id,
            "idempotency_key": "retry-worker",
            "request_fingerprint": "7" * 64,
        }
    )
    state = initialize_retry_state(
        seed,
        thread_id=source.thread_id,
        source_lineage_kind=source.lineage_kind,
        source_input_kind=source.input_kind,
        parent=None,
    )
    async with short_session(sessions) as database:
        thread = await database.get(ThreadRecord, source.thread_id)
        version, head = thread.version, thread.head_run_id
    await RunAcceptanceService(
        sessions,
        states,
        RunPayloadStore(objects),
        InlineHookValidator(EndpointPolicy()),
        bindings=ordinary_memory(sessions),
        lifecycle=test_lifecycle_writer(),
    ).advance_thread(
        run=successor,
        state=state,
        expected_thread_version=version,
        expected_current_run_id=source.id,
        expected_head_run_id=head,
        next_head_run_id=head,
    )
    requests = []

    async def model(messages, info):
        requests.append(messages)
        assert "write" in {tool.name for tool in info.function_tools}
        assert "/environment/computer" in str(messages)
        if len(requests) == 1:
            async with short_session(sessions) as database:
                mount = await database.get(RunEnvironmentMountRecord, (successor.id, "computer"))
                assert mount.application_status == "ready"
                assert mount.applied_attempt_id is not None and mount.use_started_at is not None
            yield {
                0: DeltaToolCall(
                    name="write",
                    json_args=json.dumps({"file_path": "retry.txt", "content": "fresh installation"}),
                    tool_call_id="retry-write",
                )
            }
        else:
            yield "completed"

    factory = Mock(spec=NativeModelFactory)
    factory.build.return_value = FunctionModel(stream_function=model)
    async with worker_runtime(
        sessions,
        objects,
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
                    async with short_session(sessions) as database:
                        row = await database.get(RunRecord, successor.id)
                        if row.status in {"completed", "failed"}:
                            assert row.status == "completed", row.failure_json
                            break
                    await sleep(0.02)
                await loop.drain()
                await loop.wait_stopped()
    assert len(requests) == 2
    assert (ManagedLocalDirectory(tmp_path, target.id).path / "retry.txt").read_text() == "fresh installation"
    async with short_session(sessions) as database:
        original = await database.get(RunEnvironmentMountRecord, (source.id, "computer"))
        assert original.use_started_at is None and original.applied_attempt_id is None
