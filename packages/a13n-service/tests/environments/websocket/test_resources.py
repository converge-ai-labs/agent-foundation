from __future__ import annotations

from datetime import timedelta
from time import monotonic

import pytest
from a13n_service.environments.errors import EnvironmentManagementError
from a13n_service.environments.models import EnvironmentProviderRecord, EnvironmentRecord
from a13n_service.environments.websocket.authority import LeaseDeadline
from a13n_service.environments.websocket.resources import ConnectionResources
from a13n_service.ids import new_object_id
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import utc_now

from ..conftest import ORG_ID, actor

pytestmark = pytest.mark.anyio


async def test_publication_rejects_evidence_expired_while_waiting_for_database(environment_service, target):
    resources = ConnectionResources(environment_service)
    assert not await resources.publish(
        target,
        "running",
        publication_id=new_object_id("aud"),
        evidence_deadline=LeaseDeadline(monotonic() - 1),
    )
    assert await resources.capture(target.organization_id, target.environment_id) == target


async def test_expired_worker_preparation_is_recovered_under_its_fence(environment_service, target):
    resources = ConnectionResources(environment_service)
    async with transaction(environment_service.sessions) as session:
        row = await session.get(EnvironmentRecord, target.environment_id)
        row.operation_id = new_object_id("envop")
        row.operation_generation += 1
        row.operation_action = "prepare"
        row.operation_owner = "old_worker"
        row.operation_expires_at = utc_now() - timedelta(seconds=1)
    claimed = await resources.capture(target.organization_id, target.environment_id)
    assert await resources.reconciliation_batch() == (claimed,)
    assert await resources.publish(
        claimed, "unavailable", publication_id=new_object_id("aud"), evidence_deadline=LeaseDeadline(monotonic() + 10)
    )
    current = await resources.capture(target.organization_id, target.environment_id)
    assert current.operation_id is None and current.operation_expires_at is None
    assert current.operation_generation == claimed.operation_generation + 1
    assert not await resources.publish(
        claimed, "running", publication_id=new_object_id("aud"), evidence_deadline=LeaseDeadline(monotonic() + 10)
    )


async def test_publication_replay_preserves_backing_generation(environment_service, target):
    resources = ConnectionResources(environment_service)
    publication = new_object_id("aud")
    assert await resources.publish(
        target, "running", evidence_deadline=LeaseDeadline(monotonic() + 10), publication_id=publication
    )
    assert await resources.publish(
        target, "running", evidence_deadline=LeaseDeadline(monotonic() + 10), publication_id=publication
    )
    current = await resources.capture(ORG_ID, target.environment_id)
    assert current.generation == target.generation
    assert current.target_identity == target.target_identity
    assert current.operation_generation == target.operation_generation + 1
    assert current.updated_at > target.updated_at
    with pytest.raises(ValueError, match="receipt"):
        await resources.publish(
            target, "unavailable", evidence_deadline=LeaseDeadline(monotonic() + 10), publication_id=publication
        )


async def test_stale_disconnect_cannot_overwrite_new_observation(environment_service, target):
    resources = ConnectionResources(environment_service)
    assert await resources.publish(
        target, "running", evidence_deadline=LeaseDeadline(monotonic() + 10), publication_id=new_object_id("aud")
    )
    assert not await resources.publish(
        target, "unavailable", evidence_deadline=LeaseDeadline(monotonic() + 10), publication_id=new_object_id("aud")
    )
    current = await resources.capture(ORG_ID, target.environment_id)
    assert await resources.publish(
        current, "unavailable", evidence_deadline=LeaseDeadline(monotonic() + 10), publication_id=new_object_id("aud")
    )
    async with short_session(environment_service.sessions) as session:
        row = await session.get(EnvironmentRecord, target.environment_id)
        assert row.status == "unavailable"


@pytest.mark.parametrize("mutation", ["claim", "completed_claim", "provider_disabled", "provider_updated", "deleted"])
async def test_snapshot_cannot_publish_after_authority_changes(environment_service, target, mutation):
    async with transaction(environment_service.sessions) as session:
        row = await session.get(EnvironmentRecord, target.environment_id)
        provider = await session.get(EnvironmentProviderRecord, target.provider_id)
        if mutation == "claim":
            row.operation_id = new_object_id("envop")
        elif mutation == "completed_claim":
            row.operation_generation += 1
        elif mutation == "provider_disabled":
            provider.enabled = False
        elif mutation == "provider_updated":
            provider.updated_at += timedelta(microseconds=1)
        else:
            row.status = "deleted"
    assert not await ConnectionResources(environment_service).publish(
        target, "running", evidence_deadline=LeaseDeadline(monotonic() + 10), publication_id=new_object_id("aud")
    )


async def test_capture_rejects_wrong_organization_and_disabled_provider(environment_service, target):
    resources = ConnectionResources(environment_service)
    with pytest.raises(EnvironmentManagementError) as error:
        await resources.capture("org_other", target.environment_id)
    assert error.value.code == "environment_not_found"
    async with transaction(environment_service.sessions) as session:
        provider = await session.get(EnvironmentProviderRecord, target.provider_id)
        provider.enabled = False
    with pytest.raises(EnvironmentManagementError) as error:
        await resources.authorized(actor(), target.environment_id, manage=True)
    assert error.value.code == "environment_invalid"
    disabled = await resources.authorized(actor(), target.environment_id)
    assert not disabled.provider_enabled
    assert not await resources.publish(
        disabled, "running", evidence_deadline=LeaseDeadline(monotonic() + 10), publication_id=new_object_id("aud")
    )
    assert await resources.publish(
        disabled, "unavailable", evidence_deadline=LeaseDeadline(monotonic() + 10), publication_id=new_object_id("aud")
    )
    page = await resources.reconciliation_batch()
    assert len(page) == 1
    assert not page[0].provider_enabled


async def test_reconciliation_only_scans_retained_observations(environment_service, target):
    resources = ConnectionResources(environment_service)
    assert await resources.reconciliation_batch() == (target,)
    assert await resources.publish(
        target, "running", evidence_deadline=LeaseDeadline(monotonic() + 10), publication_id=new_object_id("aud")
    )
    page = await resources.reconciliation_batch(limit=1)
    assert [item.environment_id for item in page] == [target.environment_id]
    assert await resources.reconciliation_batch(after_id=target.environment_id) == ()
    async with transaction(environment_service.sessions) as session:
        row = await session.get(EnvironmentRecord, target.environment_id)
        row.operation_id = new_object_id("envop")
    assert await resources.reconciliation_batch() == ()
