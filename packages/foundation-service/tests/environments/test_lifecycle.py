from datetime import UTC, datetime, timedelta

import pytest
from a13n_environment_provider import (
    Environment,
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentOperations,
    EnvironmentPermissionSet,
    EnvironmentState,
)
from a13n_service.environments.domain import CreateProviderRequest, CreateTemplateRequest, NewEnvironmentSelection
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.storage import short_session, transaction

from .conftest import WORKSPACE_ID, actor

pytestmark = pytest.mark.anyio


class Target(Environment):
    def __init__(self, state, events):
        super().__init__(state)
        self.events = events

    @property
    def provider_key(self):
        return "a13n.docker"

    @property
    def environment_id(self):
        return "env-test"

    @property
    def descriptor(self):
        return EnvironmentDescriptor(
            generation="generation-1", operation_families=frozenset(), permissions=EnvironmentPermissionSet()
        )

    @property
    def availability(self):
        return EnvironmentAvailability(status="available")

    @property
    def operations(self):
        return EnvironmentOperations()

    async def _prepare(self, **kwargs):
        self.events.append("prepare")

    async def _ensure_ready(self, operations):
        return None

    async def _close(self):
        self.events.append("close")

    async def _stop(self):
        self.events.append("stop")

    async def _destroy(self):
        self.events.append("delete")


async def fixture_environment(service):
    provider = await service.create_provider(
        actor=actor(), workspace_id=WORKSPACE_ID, request=CreateProviderRequest(type="a13n.docker", name="Docker")
    )
    template = await service.create_template(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="recipe",
        request=CreateTemplateRequest(
            name="Sandbox",
            provider_id=provider.id,
            configuration={},
            retention={"idle": {"stop_after": 60, "delete_after": 120}},
        ),
    )
    return await service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=NewEnvironmentSelection(template_id=template.id),
        idempotency_key="allocate",
    )


async def test_maintenance_stops_then_deletes_from_original_condition_time(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch
):
    environment = await fixture_environment(environment_service)
    start = datetime(2026, 9, 5, tzinfo=UTC)
    now = start + timedelta(seconds=60)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, tmp_path, clock=lambda: now)
    events = []

    async def construct(operation):
        return Target(operation.state, events)

    monkeypatch.setattr(lifecycle, "construct", construct)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        row.status, row.condition_since = "running", start
        row.state = EnvironmentState(
            provider_key="a13n.docker", state_version="1", state={"target": "same"}
        ).model_dump(mode="json")
        row.generation = 1
    await lifecycle.maintain(environment.id)
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.status == "stopped" and row.generation == 1
        assert row.condition_since.replace(tzinfo=UTC) == start
        assert row.state is not None
    now = start + timedelta(seconds=120)
    await lifecycle.maintain(environment.id)
    assert events == ["stop", "close", "delete", "close"]
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.status == "deleted" and row.state is None and row.generation == 1
        assert row.condition_since.replace(tzinfo=UTC) == start


async def test_stale_lifecycle_publication_cannot_change_target(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path
):
    environment = await fixture_environment(environment_service)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, tmp_path, clock=lambda: now)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        row.status, row.condition_since = "running", now - timedelta(seconds=60)
    operation = await lifecycle.acquire(environment.id, "stop")
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        row.operation_generation += 1
    with pytest.raises(RuntimeError, match="authority changed"):
        await lifecycle.publish(operation, Target(None, []))
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.status == "running" and row.operation_id == operation.operation_id


async def test_known_stop_failure_releases_operation_and_records_failed_command(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch
):
    from a13n_environment_provider.docker._errors import missing_failure
    from a13n_service.environments.domain import EnvironmentCommandRequest
    from a13n_service.environments.models import EnvironmentCommandRecord

    environment = await fixture_environment(environment_service)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, tmp_path, clock=lambda: now)

    class MissingTarget(Target):
        async def _stop(self):
            raise missing_failure("Docker target is missing")

    async def construct(operation):
        return MissingTarget(operation.state, [])

    monkeypatch.setattr(lifecycle, "construct", construct)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        row.status = "running"
        row.state = EnvironmentState(
            provider_key="a13n.docker", state_version="1", state={"target": "lost"}
        ).model_dump(mode="json")
    command = await environment_service.request_command(
        actor=actor(),
        environment_id=environment.id,
        request=EnvironmentCommandRequest(action="stop"),
        idempotency_key="stop-missing",
    )
    with pytest.raises(Exception, match="Docker target is missing"):
        await lifecycle.maintain(environment.id)
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        receipt = await session.get(EnvironmentCommandRecord, command.id)
        assert row.operation_id is None
        assert receipt.status == "failed" and receipt.completed_at is not None
    await environment_service.request_command(
        actor=actor(),
        environment_id=environment.id,
        request=EnvironmentCommandRequest(action="delete"),
        idempotency_key="delete-missing",
    )


@pytest.mark.parametrize("had_target", [False, True])
@pytest.mark.parametrize("publication_fails", [False, True])
async def test_abandoned_preparation_is_observed_without_starting_target(
    environment_service,
    environment_sessions,
    provider_catalog,
    protector,
    tmp_path,
    monkeypatch,
    had_target,
    publication_fails,
):
    environment = await fixture_environment(environment_service)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, tmp_path, clock=lambda: now)
    events = []

    class ObservedTarget(Target):
        async def reconcile(self):
            events.append("observe")
            return "absent"

    async def construct(operation):
        return ObservedTarget(operation.state, events)

    monkeypatch.setattr(lifecycle, "construct", construct)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        row.status = "unavailable"
        if had_target:
            row.state = EnvironmentState(
                provider_key="a13n.docker", state_version="1", state={"target": "gone"}
            ).model_dump(mode="json")
            row.target_identity = "a" * 64
            row.generation = 3
            row.expires_at = now
        row.operation_id, row.operation_action = "envop-abandoned", "prepare"
        row.operation_expires_at = now - timedelta(seconds=1)
    if publication_fails:
        publish = lifecycle.publish

        async def fail_once(*args, **kwargs):
            monkeypatch.setattr(lifecycle, "publish", publish)
            raise RuntimeError("Publication unavailable")

        monkeypatch.setattr(lifecycle, "publish", fail_once)
        with pytest.raises(RuntimeError, match="Publication unavailable"):
            await lifecycle.maintain(environment.id)
    else:
        await lifecycle.maintain(environment.id)
    assert events == ["observe", "close"]
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.operation_id is None and row.status == "deleted"
        assert row.state is row.target_identity is row.expires_at is row.next_maintenance_at is None
        assert row.generation == (3 if had_target else 0)


async def test_absent_docker_allocation_releases_capacity_and_delete_is_idempotent(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch
):
    from unittest.mock import Mock

    from a13n_environment_provider import EnvironmentError
    from a13n_environment_provider.docker.runtime import DockerEngine, DockerSDKEngine
    from a13n_service.environments.capacity import CapacityLimits
    from a13n_service.environments.domain import EnvironmentCommandRequest
    from a13n_service.environments.models import EnvironmentCommandRecord, EnvironmentTemplateRevisionRecord

    first = await fixture_environment(environment_service)
    async with short_session(environment_sessions) as session:
        revision = await session.get(EnvironmentTemplateRevisionRecord, first.template_revision_id)
        template_id = revision.template_id
    second = await environment_service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=NewEnvironmentSelection(template_id=template_id),
        idempotency_key="second-allocation",
    )
    now = datetime(2026, 9, 5, tzinfo=UTC)
    capacity = CapacityLimits(max_targets=1)
    lifecycle = EnvironmentLifecycle(
        environment_sessions, provider_catalog, protector, tmp_path, clock=lambda: now, capacity=capacity
    )
    engine = Mock(spec=DockerEngine)
    engine.find_containers.return_value = ()
    monkeypatch.setattr(DockerSDKEngine, "connect", lambda _: engine)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, first.id)
        row.status = "unavailable"
        row.operation_id, row.operation_action = "envop-abandoned", "prepare"
        row.operation_expires_at = now - timedelta(seconds=1)

    async def admit(environment_id):
        async with transaction(environment_sessions) as session:
            await capacity.lock_workspace(session, environment_id)
            await capacity.admit(session, await session.get(EnvironmentRecord, environment_id))

    with pytest.raises(EnvironmentError, match="capacity is exhausted"):
        await admit(second.id)
    await lifecycle.maintain(first.id)
    command = await environment_service.request_command(
        actor=actor(),
        environment_id=first.id,
        idempotency_key="delete-absent",
        request=EnvironmentCommandRequest(action="delete"),
    )
    await lifecycle.maintain(first.id)
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, first.id)
        assert row.status == "deleted" and row.operation_id is row.state is None
        assert (await session.get(EnvironmentCommandRecord, command.id)).status == "completed"
    engine.find_containers.assert_awaited_once()
    engine.create_container.assert_not_awaited()
    engine.stop_container.assert_not_awaited()
    engine.remove_container.assert_not_awaited()
    await admit(second.id)
    with pytest.raises(EnvironmentError, match="capacity is exhausted"):
        await admit(first.id)


async def test_retention_transition_uses_aggregate_entry_time():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from a13n_service.environments.retention import refresh_retention

    now = datetime(2026, 9, 5, tzinfo=UTC)
    session = SimpleNamespace(flush=AsyncMock(), scalar=AsyncMock(return_value=None))
    row = SimpleNamespace(id="env-shared", retention_condition="active", condition_since=now - timedelta(hours=3))
    await refresh_retention(session, row, now)
    assert row.retention_condition == "idle" and row.condition_since == now
    await refresh_retention(session, row, now + timedelta(seconds=10))
    assert row.condition_since == now


async def test_periodic_batches_advance_past_failures_and_exclude_deleted_and_external(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    from a13n_service.environments.domain import NewEnvironmentSelection, RegisterEnvironmentRequest
    from a13n_service.environments.maintenance import EnvironmentMaintenanceLoop

    first = await fixture_environment(environment_service)
    async with short_session(environment_sessions) as session:
        from a13n_service.environments.models import EnvironmentTemplateRevisionRecord

        revision = await session.get(EnvironmentTemplateRevisionRecord, first.template_revision_id)
        template_id = revision.template_id
    second = await environment_service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=NewEnvironmentSelection(template_id=template_id),
        idempotency_key="second",
    )
    third = await environment_service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=NewEnvironmentSelection(template_id=template_id),
        idempotency_key="deleted",
    )
    external = await environment_service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=RegisterEnvironmentRequest(provider_id=first.provider_id, configuration={"image": "debian:bookworm"}),
        idempotency_key="external",
    )
    now = datetime(2026, 9, 6, tzinfo=UTC)
    async with transaction(environment_sessions) as session:
        for index, environment_id in enumerate((first.id, second.id, third.id, external.id)):
            row = await session.get(EnvironmentRecord, environment_id)
            row.next_maintenance_at = now - timedelta(seconds=10 - index)
        (await session.get(EnvironmentRecord, third.id)).status = "deleted"
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, tmp_path, clock=lambda: now)
    maintain = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    monkeypatch.setattr(lifecycle, "maintain", maintain)
    loop = EnvironmentMaintenanceLoop(lifecycle, concurrency=1, batch_size=1)
    await loop.run_once()
    await loop.run_once()
    await loop.run_once()
    assert {call.args[0] for call in maintain.await_args_list} == {first.id, second.id}
    now += timedelta(seconds=5)
    await loop.run_once()
    assert maintain.await_count == 2
    now += timedelta(seconds=25)
    await loop.run_once()
    assert maintain.await_count == 4


async def test_unknown_stop_retains_operation_receipt_until_reconciled(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch
):
    from a13n_environment_provider.docker._errors import unknown_failure
    from a13n_service.environments.domain import EnvironmentCommandRequest
    from a13n_service.environments.models import EnvironmentCommandRecord

    environment = await fixture_environment(environment_service)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, tmp_path, clock=lambda: now)
    failed = False

    class UncertainTarget(Target):
        async def _stop(self):
            nonlocal failed
            if not failed:
                failed = True
                raise unknown_failure("Stop response lost")

    async def construct(operation):
        return UncertainTarget(operation.state, [])

    monkeypatch.setattr(lifecycle, "construct", construct)
    state = EnvironmentState(provider_key="a13n.docker", state_version="1", state={"target": "same"})
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        row.status, row.state = "running", state.model_dump(mode="json")
    command = await environment_service.request_command(
        actor=actor(),
        environment_id=environment.id,
        idempotency_key="uncertain-stop",
        request=EnvironmentCommandRequest(action="stop"),
    )
    with pytest.raises(Exception, match="Stop response lost"):
        await lifecycle.maintain(environment.id)
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.operation_id == command.id and row.state == state.model_dump(mode="json")
        assert row.last_error["code"] == "environment_operation_unresolved"
        assert (await session.get(EnvironmentCommandRecord, command.id)).status == "pending"
    now += timedelta(seconds=71)
    await lifecycle.maintain(environment.id)
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.status == "stopped" and row.operation_id is None
        assert (await session.get(EnvironmentCommandRecord, command.id)).status == "completed"


@pytest.mark.parametrize("lifetime", [30, 300, 3600])
async def test_observed_expiry_schedules_renewal_without_repeated_provider_calls(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch, lifetime
):
    from a13n_service.environments.domain import NewEnvironmentSelection
    from a13n_service.environments.maintenance import EnvironmentMaintenanceLoop

    first = await fixture_environment(environment_service)
    # No idle action should supersede the expiry schedule in this test.
    template = await environment_service.create_template(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="retained",
        request=CreateTemplateRequest(
            name="Retained",
            provider_id=first.provider_id,
            configuration={},
            retention={"idle": {"stop_after": None, "delete_after": None}},
        ),
    )
    environment = await environment_service.create_environment(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="retained-target",
        request=NewEnvironmentSelection(template_id=template.id),
    )
    now = datetime(2026, 9, 5, tzinfo=UTC)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, tmp_path, clock=lambda: now)
    events = []

    class ExpiringTarget(Target):
        @property
        def keepalive_horizon(self):
            return timedelta(seconds=lifetime)

        async def keepalive(self, *, deadline, operation_id):
            events.append(operation_id)
            return deadline

    async def construct(operation):
        return ExpiringTarget(operation.state, [])

    monkeypatch.setattr(lifecycle, "construct", construct)
    monkeypatch.setattr(provider_catalog.require("a13n.docker"), "requires_keepalive", True)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        row.status, row.next_maintenance_at = "running", now
    loop = EnvironmentMaintenanceLoop(lifecycle)
    await loop.run_once()
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.expires_at.replace(tzinfo=UTC) == now + timedelta(seconds=lifetime)
        next_due = row.next_maintenance_at.replace(tzinfo=UTC)
        assert next_due == now + timedelta(seconds=lifetime - min(60, lifetime / 5))
    await loop.run_once()
    now = next_due - timedelta(microseconds=1)
    await loop.run_once()
    assert len(events) == 1
    now = next_due
    await loop.run_once()
    assert len(events) == 2 and len(set(events)) == 2


async def test_competing_maintenance_does_not_shorten_pending_operation_deadline(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch
):
    import asyncio

    from a13n_service.environments.maintenance import EnvironmentMaintenanceLoop

    environment = await fixture_environment(environment_service)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, tmp_path, clock=lambda: now)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    class SlowTarget(Target):
        async def keepalive(self, *, deadline, operation_id):
            calls.append(operation_id)
            started.set()
            await release.wait()
            return deadline

    async def construct(operation):
        return SlowTarget(operation.state, [])

    monkeypatch.setattr(lifecycle, "construct", construct)
    monkeypatch.setattr(provider_catalog.require("a13n.docker"), "requires_keepalive", True)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        row.status, row.condition_since, row.next_maintenance_at = "running", now, now
    first = asyncio.create_task(EnvironmentMaintenanceLoop(lifecycle).run_once())
    try:
        await asyncio.wait_for(started.wait(), 3)
        await EnvironmentMaintenanceLoop(lifecycle).run_once()
        async with short_session(environment_sessions) as session:
            row = await session.get(EnvironmentRecord, environment.id)
            assert row.operation_id == calls[0]
            assert row.next_maintenance_at >= row.operation_expires_at
    finally:
        release.set()
        await first
    assert len(calls) == 1


@pytest.mark.parametrize("status", ["unprepared", "deleted", "running"])
async def test_stop_preserves_absent_status_and_still_stops_stateless_targets(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch, status
):
    from a13n_service.environments.domain import EnvironmentCommandRequest

    environment = await fixture_environment(environment_service)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, tmp_path, clock=lambda: now)
    events = []

    async def construct(operation):
        return Target(None, events)

    monkeypatch.setattr(lifecycle, "construct", construct)
    async with transaction(environment_sessions) as session:
        (await session.get(EnvironmentRecord, environment.id)).status = status
    await environment_service.request_command(
        actor=actor(),
        environment_id=environment.id,
        idempotency_key="stop-absent",
        request=EnvironmentCommandRequest(action="stop"),
    )
    await lifecycle.maintain(environment.id)
    assert events == (["stop", "close"] if status == "running" else ["close"])
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.status == ("stopped" if status == "running" else status)
        assert row.operation_id is None


async def test_slow_target_does_not_block_available_workers_at_a_page_boundary(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch
):
    import asyncio

    from a13n_service.environments.maintenance import EnvironmentMaintenanceLoop
    from a13n_service.environments.models import EnvironmentTemplateRevisionRecord

    first = await fixture_environment(environment_service)
    async with short_session(environment_sessions) as session:
        revision = await session.get(EnvironmentTemplateRevisionRecord, first.template_revision_id)
        template_id = revision.template_id
    ids = [first.id]
    for index in range(2):
        environment = await environment_service.create_environment(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key=f"queued-{index}",
            request=NewEnvironmentSelection(template_id=template_id),
        )
        ids.append(environment.id)
    ids.sort()
    now = datetime(2026, 9, 5, tzinfo=UTC)
    async with transaction(environment_sessions) as session:
        for environment_id in ids:
            (await session.get(EnvironmentRecord, environment_id)).next_maintenance_at = now
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, tmp_path, clock=lambda: now)
    release, progressed = asyncio.Event(), asyncio.Event()
    visited = []

    async def maintain(environment_id):
        visited.append(environment_id)
        if environment_id == ids[0]:
            await release.wait()
        if environment_id == ids[-1]:
            progressed.set()

    monkeypatch.setattr(lifecycle, "maintain", maintain)
    task = asyncio.create_task(EnvironmentMaintenanceLoop(lifecycle, batch_size=1, concurrency=2).run_once())
    try:
        await asyncio.wait_for(progressed.wait(), 3)
        assert not task.done()
    finally:
        release.set()
        await task
    assert sorted(visited) == ids
