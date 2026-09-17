"""Exercise the real hosted E2B adapter; fake only the external sandbox API."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import e2b
import pytest
from a13n_environment import EnvironmentError, build_environment_provider_catalog
from a13n_environment._guest_files import GuestFiles
from a13n_service.agents.models import AgentRecord
from a13n_service.environments.domain import CreateProviderRequest, CreateTemplateRequest
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.environments.runtime import prepare_run_environment
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.storage import short_session, transaction
from e2b.exceptions import SandboxNotFoundException

from tests.hooks.support import hook_actor
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import AGENT_ID, NOW, WORKSPACE_ID
from .test_attempt_execution import _accept_root, _authority, _worker
from .test_environment_runtime import template_config
from .worker_helpers import prepare_permissions

pytestmark = pytest.mark.anyio


class SandboxAPI:
    def __init__(self):
        self.targets = {}
        self.created = 0
        self.connected = 0

    async def create(self, *, metadata, **kwargs):
        self.created += 1
        target = SimpleNamespace(sandbox_id=f"sandbox-{self.created}", metadata=metadata, ready=True)

        async def is_running(**kwargs):
            return target.ready

        target.is_running = is_running
        self.targets[target.sandbox_id] = target
        return target

    async def get_info(self, sandbox_id, **kwargs):
        if sandbox_id not in self.targets:
            raise SandboxNotFoundException("gone")
        return self.targets[sandbox_id]

    async def connect(self, sandbox_id, **kwargs):
        self.connected += 1
        target = self.targets[sandbox_id]
        target.ready = True
        return target

    def list(self, *, query, **kwargs):
        matches = [t for t in self.targets.values() if all(t.metadata.get(k) == v for k, v in query.metadata.items())]
        return SimpleNamespace(has_next=False, next_items=AsyncMock(return_value=matches))


@pytest.mark.parametrize("missing", [False, True])
async def test_host_publishes_recovery_before_exposing_the_new_backing(
    interaction_sessions, interaction_object_store, tmp_path, monkeypatch, missing
):
    service, _, lifecycle = await template_config(interaction_sessions, tmp_path, "on_use")
    service.catalog = lifecycle.catalog = build_environment_provider_catalog(builtin_keys=("e2b",))
    provider = await service.create_provider(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateProviderRequest(type="e2b", name="E2B", credential={"api_key": "test-key"}),
    )
    template = await service.create_template(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="e2b-template",
        request=CreateTemplateRequest(
            name="Sandbox",
            provider_id=provider.id,
            configuration={},
            preparation="on_use",
            retention={"idle": {"stop_after": None, "delete_after": None}},
        ),
    )
    async with transaction(interaction_sessions) as session:
        (await session.get(AgentRecord, AGENT_ID)).default_environment_template_id = template.id
    api = SandboxAPI()
    monkeypatch.setattr(e2b, "AsyncSandbox", api)
    monkeypatch.setattr(GuestFiles, "stat", AsyncMock())
    _, run, _ = await _accept_root(interaction_sessions, interaction_object_store)
    claim = await AttemptScheduler(
        interaction_sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    env = await prepare_run_environment(
        lifecycle, await prepare_permissions(interaction_sessions, run, _authority(claim))
    )
    await env.enter(thread_id=run.thread_id, run_id=run.id, agent_instance_id="agent", mount_id="workspace")
    await env.ensure_ready(frozenset({"files"}))
    old_processes = env.operations.processes
    old_files = env.operations.files
    old_identity = env.descriptor.backing_identity
    old_generation = env.descriptor.generation
    api.targets["sandbox-1"].ready = False
    if missing:
        del api.targets["sandbox-1"]
    with pytest.raises(EnvironmentError) as error:
        await env.ensure_ready(frozenset({"files"}))
    assert error.value.code == ("environment_rebuilt" if missing else "environment_connection_refreshed")
    assert (env.descriptor.backing_identity != old_identity) == missing
    assert (env.descriptor.generation != old_generation) == missing
    assert (env.operations.processes is old_processes) != missing
    assert env.operations.files is not old_files
    async with short_session(interaction_sessions) as session:
        record = await session.get(EnvironmentRecord, env.environment_id)
        assert record.state == env.dump_state().model_dump(mode="json")
        assert record.generation == env.backing_generation == (2 if missing else 1)
        assert record.operation_generation == 2 and record.operation_id is None
        assert env.descriptor.backing_identity == f"{record.id}:{record.generation}"
    await env.ensure_ready(frozenset({"files"}))
    assert api.created == (2 if missing else 1)
    await env.close()
