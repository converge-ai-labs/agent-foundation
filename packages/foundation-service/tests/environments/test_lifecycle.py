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
            retention={"idle": {"stop_after": 60, "delete_after": 120}, "waiting_approval": {"stop_after": 90}},
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
