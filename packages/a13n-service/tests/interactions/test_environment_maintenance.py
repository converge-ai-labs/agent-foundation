"""PostgreSQL locks make maintenance decisions and ownership one atomic transition."""

import asyncio
from datetime import timedelta

import pytest
from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment.builtins import select_builtin_environment_providers
from a13n_service.environments import lifecycle as lifecycle_module
from a13n_service.environments.domain import (
    CreateManagedEnvironmentRequest,
    CreateProviderRequest,
    CreateTemplateRequest,
)
from a13n_service.environments.lifecycle import EnvironmentLifecycle, EnvironmentOperationBusy
from a13n_service.environments.maintenance import EnvironmentMaintenanceLoop
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.environments.service import EnvironmentService
from a13n_service.interactions.models import RunRecord
from a13n_service.interactions.scheduling import AttemptScheduler
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import short_session, transaction
from sqlalchemy import select

from tests.environments.test_lifecycle import Target
from tests.hooks.support import hook_actor, seed_hook_actor_access
from tests.lifecycle_support import test_lifecycle_writer

from .conftest import NOW, WORKSPACE_ID
from .test_attempt_execution import _accept_root, _authority, _worker
from .worker_helpers import prepare_permissions

pytestmark = pytest.mark.anyio


@pytest.fixture
async def due_environment(interaction_sessions, tmp_path):
    sessions = interaction_sessions
    await seed_hook_actor_access(sessions)
    catalog = ProviderCatalog(select_builtin_environment_providers(("docker",)))
    protector = SecretProtector(key=b"e" * 32, encryption_key_id="test")
    service = EnvironmentService(sessions, catalog, protector)
    provider = await service.create_provider(
        actor=hook_actor(), workspace_id=WORKSPACE_ID, request=CreateProviderRequest(type="docker", name="Docker")
    )
    template = await service.create_template(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="maintenance-template",
        request=CreateTemplateRequest(
            name="Sandbox",
            provider_id=provider.id,
            configuration={},
            retention={"idle": {"stop_after": 60, "delete_after": 120}},
        ),
    )
    environment = await service.create_environment(
        actor=hook_actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="maintenance-target",
        request=CreateManagedEnvironmentRequest(template_id=template.id),
    )
    now = NOW + timedelta(seconds=2)
    async with transaction(sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        row.status, row.condition_since, row.next_maintenance_at = "running", NOW - timedelta(seconds=60), now
    return sessions, environment.id, EnvironmentLifecycle(sessions, catalog, protector, clock=lambda: now)


async def test_maintenance_decision_and_claim_commit_together_and_skip_competitors(due_environment, monkeypatch):
    sessions, environment_id, lifecycle = due_environment
    now = lifecycle.clock()
    deciding, release = asyncio.Event(), asyncio.Event()
    refresh = lifecycle_module.refresh_retention

    async def paused_refresh(session, row, at):
        condition = await refresh(session, row, at)
        deciding.set()
        await release.wait()
        return condition

    monkeypatch.setattr(lifecycle_module, "refresh_retention", paused_refresh)
    first = asyncio.create_task(lifecycle.acquire_maintenance(environment_id, cutoff=now))
    try:
        await asyncio.wait_for(deciding.wait(), 3)
        async with short_session(sessions) as session:
            row = await session.get(EnvironmentRecord, environment_id)
            assert row.operation_id is None and row.next_maintenance_at == now
        assert await asyncio.wait_for(lifecycle.acquire_maintenance(environment_id, cutoff=now), 3) is None
    finally:
        release.set()
        operation = await first
    assert operation is not None and operation.action == "stop"
    async with short_session(sessions) as session:
        row = await session.get(EnvironmentRecord, environment_id)
        assert row.operation_id == operation.operation_id and row.operation_generation == operation.fence == 1
        assert row.operation_owner == operation.owner and row.status == "running"
        assert row.next_maintenance_at == row.operation_expires_at == now + lifecycle.lease_duration
    assert await lifecycle.acquire_maintenance(environment_id, cutoff=now) is None


async def test_run_preparation_cannot_enter_between_maintenance_decision_and_claim(
    due_environment, interaction_object_store, monkeypatch
):
    sessions, environment_id, lifecycle = due_environment
    _, run, _ = await _accept_root(sessions, interaction_object_store, environment_id=environment_id)
    claim = await AttemptScheduler(
        sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer()
    ).claim(run.id, _worker())
    attempt = await prepare_permissions(sessions, run, _authority(claim))
    deciding, preparing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    refresh = lifecycle_module.refresh_retention
    lock_workspace = type(lifecycle.capacity).lock_workspace

    async def paused_refresh(session, row, at):
        condition = await refresh(session, row, at)
        deciding.set()
        await release.wait()
        return condition

    async def locked_workspace(self, session, target_id):
        await lock_workspace(self, session, target_id)
        preparing.set()

    monkeypatch.setattr(lifecycle_module, "refresh_retention", paused_refresh)
    monkeypatch.setattr(type(lifecycle.capacity), "lock_workspace", locked_workspace)
    maintenance = asyncio.create_task(lifecycle.acquire_maintenance(environment_id, cutoff=lifecycle.clock()))
    preparation = None
    try:
        await asyncio.wait_for(deciding.wait(), 3)
        preparation = asyncio.create_task(lifecycle.acquire_preparation(environment_id, attempt=attempt))
        await asyncio.wait_for(preparing.wait(), 3)
        assert not preparation.done()
    finally:
        release.set()
        operation = await asyncio.wait_for(maintenance, 3)
        if preparation is not None:
            result = (await asyncio.wait_for(asyncio.gather(preparation, return_exceptions=True), 3))[0]
    assert operation is not None and operation.action == "stop"
    assert isinstance(result, EnvironmentOperationBusy)
    async with short_session(sessions) as session:
        assert (await session.get(RunRecord, run.id)).environment_use_started_at is None
        assert (await session.get(EnvironmentRecord, environment_id)).operation_id == operation.operation_id


async def test_competing_loops_dispatch_once_without_holding_the_environment_lock(due_environment, monkeypatch):
    sessions, environment_id, lifecycle = due_environment
    started, release = asyncio.Event(), asyncio.Event()
    events = []

    class SlowTarget(Target):
        async def _stop(self):
            events.append("stop")
            started.set()
            await release.wait()

    async def construct(operation):
        return SlowTarget(None, events)

    monkeypatch.setattr(lifecycle, "construct", construct)
    first = asyncio.create_task(EnvironmentMaintenanceLoop(lifecycle).run_once())
    try:
        await asyncio.wait_for(started.wait(), 3)
        async with transaction(sessions) as session:
            row = await session.scalar(
                select(EnvironmentRecord).where(EnvironmentRecord.id == environment_id).with_for_update(nowait=True)
            )
            assert row.operation_action == "stop" and row.status == "running"
            assert row.next_maintenance_at == row.operation_expires_at
        await EnvironmentMaintenanceLoop(lifecycle).run_once()
    finally:
        release.set()
        await asyncio.wait_for(first, 3)
    assert events == ["stop", "close"]
    async with short_session(sessions) as session:
        row = await session.get(EnvironmentRecord, environment_id)
        assert row.status == "stopped" and row.operation_id is None


async def test_existing_file_lease_prevents_retention_until_release(due_environment, tmp_path, monkeypatch):
    from a13n_harness.providers.environment.direct_local.files import LocalFileOperator
    from a13n_harness.providers.environment.direct_local.provider import _DirectLocalFilePolicy
    from a13n_harness.providers.environment.models import (
        EnvironmentDescriptor,
        EnvironmentPermissionSet,
        EnvironmentState,
    )
    from a13n_harness.providers.environment.operations import EnvironmentOperations
    from a13n_service.environments.file_access import ExistingEnvironmentFiles
    from a13n_service.environments.models import EnvironmentFileUseRecord
    from a13n_service.environments.retention import has_active_use

    sessions, environment_id, lifecycle = due_environment
    state = EnvironmentState(provider_key="docker", state_version="1", state={"target": "same"})
    async with transaction(sessions) as session:
        row = await session.get(EnvironmentRecord, environment_id)
        row.state = state.model_dump(mode="json")
        generation = row.generation
    files = LocalFileOperator(
        root=tmp_path,
        policy=_DirectLocalFilePolicy(max_value_bytes=1024),
        mount_id="test",
        generation="same",
    )

    class FileTarget(Target):
        @property
        def operations(self):
            return EnvironmentOperations(files=files)

        @property
        def descriptor(self):
            return EnvironmentDescriptor(
                generation="same", operation_families=frozenset({"files"}), permissions=EnvironmentPermissionSet()
            )

    async def construct(operation, *, allow_create=True):
        assert not allow_create
        return FileTarget(operation.state, [])

    monkeypatch.setattr(lifecycle, "construct", construct)

    async def authorize():
        pass

    access = ExistingEnvironmentFiles(lifecycle)
    async with access.open(
        actor=hook_actor(),
        environment_id=environment_id,
        backing_identity=f"{environment_id}:{generation}",
        authorize=authorize,
    ):
        async with short_session(sessions) as session:
            assert await has_active_use(session, environment_id)
        assert await lifecycle.acquire_maintenance(environment_id) is None
    async with short_session(sessions) as session:
        assert not list(await session.scalars(select(EnvironmentFileUseRecord)))
    with pytest.raises(ValueError, match="unavailable"):
        async with access.open(
            actor=hook_actor(),
            environment_id=environment_id,
            backing_identity=f"{environment_id}:{generation + 1}",
            authorize=authorize,
        ):
            pytest.fail("Recreated targets cannot satisfy a retained memory binding")
