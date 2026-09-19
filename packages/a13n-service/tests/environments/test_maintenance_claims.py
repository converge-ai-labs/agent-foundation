"""Maintenance planning either grants ownership, schedules a deadline, or rolls back."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from a13n_service.environments.lifecycle import EnvironmentLifecycle
from a13n_service.environments.models import EnvironmentRecord
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import assume_utc
from sqlalchemy.exc import OperationalError

from .conftest import NOW
from .test_lifecycle import fixture_environment

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("failure", ["configuration", "snapshot", "database"])
async def test_planning_failure_never_partially_claims_an_operation(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, monkeypatch, pending, failure
):
    environment = await fixture_environment(environment_service)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, clock=lambda: NOW)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        row.status, row.condition_since, row.next_maintenance_at = ("running", NOW - timedelta(seconds=60), NOW)
        row.operation_generation = 4
        if pending:
            row.operation_id, row.operation_action, row.operation_owner = ("envop-existing", "stop", "envowner-old")
            row.operation_expires_at = NOW - timedelta(seconds=1)
        if failure == "snapshot":
            row.state = {"invalid": "provider state"}
    if failure != "snapshot":
        error = (
            OperationalError(None, None, RuntimeError("Database unavailable"))
            if failure == "database"
            else ValueError("Template configuration unavailable")
        )
        monkeypatch.setattr("a13n_service.environments.lifecycle.load_configuration", AsyncMock(side_effect=error))
    with pytest.raises(OperationalError if failure == "database" else ValueError):
        await lifecycle.acquire_maintenance(environment.id, cutoff=NOW)
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.operation_generation == 4
        assert row.operation_id == ("envop-existing" if pending else None)
        assert row.operation_owner == ("envowner-old" if pending else None)
        if pending:
            assert assume_utc(row.operation_expires_at) == NOW - timedelta(seconds=1)
        else:
            assert row.operation_expires_at is None
        assert assume_utc(row.next_maintenance_at) == NOW + timedelta(seconds=0 if failure == "database" else 30)


@pytest.mark.parametrize("status", ["unprepared", "deleted", "running"])
async def test_no_action_only_schedules_the_next_deadline(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path, status
):
    environment = await fixture_environment(environment_service)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, clock=lambda: NOW)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        row.status, row.condition_since, row.next_maintenance_at = (status, NOW - timedelta(seconds=30), NOW)
    assert await lifecycle.acquire_maintenance(environment.id, cutoff=NOW) is None
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.status == status and row.operation_id is None and row.operation_generation == 0
        assert (assume_utc(row.next_maintenance_at) if row.next_maintenance_at else None) == (
            NOW + timedelta(seconds=30) if status == "running" else None
        )


async def test_stale_scan_cannot_claim_after_the_deadline_changes(
    environment_service, environment_sessions, provider_catalog, protector, tmp_path
):
    environment = await fixture_environment(environment_service)
    lifecycle = EnvironmentLifecycle(environment_sessions, provider_catalog, protector, clock=lambda: NOW)
    async with transaction(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        row.status, row.condition_since = "running", NOW - timedelta(seconds=60)
        row.next_maintenance_at = NOW + timedelta(seconds=30)
    assert await lifecycle.acquire_maintenance(environment.id, cutoff=NOW) is None
    async with short_session(environment_sessions) as session:
        row = await session.get(EnvironmentRecord, environment.id)
        assert row.operation_id is None and row.operation_generation == 0
        assert assume_utc(row.next_maintenance_at) == NOW + timedelta(seconds=30)
