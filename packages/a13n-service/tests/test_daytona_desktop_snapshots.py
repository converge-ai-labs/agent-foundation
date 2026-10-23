"""Automatic Daytona desktop snapshot preparation and durable creation feedback."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.providers.environment.native.errors import failure
from a13n_service.infra.db import now, transaction
from a13n_service.providers.environment.daytona_build import Snapshot
from a13n_service.runs.environments import daytona_snapshots as builds
from a13n_service.runs.environments import lifecycle
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow

pytestmark = pytest.mark.anyio


class Cloud:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.snapshot = None
        self.error = None
        self.gate = None
        self.started = asyncio.Event()
        self.delete_error = False

    async def create(self, name, manifest):
        self.calls.append(("create", name))
        self.started.set()
        self.snapshot = Snapshot(id="test-snapshot", state="building")
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.snapshot

    async def find(self, name, manifest, identity=None):
        self.calls.append(("find", identity))
        return self.snapshot

    async def delete(self, name, manifest, identity):
        self.calls.append(("delete", identity))
        if self.delete_error:
            raise failure("daytona", "cleanup_pending", Category.UNAVAILABLE)
        self.snapshot = None

    async def close(self):
        self.closed += 1


@pytest.fixture
async def setup(service, monkeypatch, runs_kit):
    await runs_kit.pause_sweeps(service)
    cloud = Cloud()
    monkeypatch.setattr(builds, "_builder", lambda *_: cloud)
    client = service.client
    p = await client.post(
        "/api/v1/environment-providers",
        json={
            "type": "daytona",
            "name": "Daytona",
            "credential": {"api_key": "test-key"},
            "config": {},
        },
    )
    assert p.status_code == 201, p.text
    t = await client.post(
        "/api/v1/environment-templates",
        json={
            "name": "Desktop",
            "provider_id": p.json()["id"],
            "config": {"recipe": {"desktop": True}},
        },
    )
    assert t.status_code == 201, t.text

    async def create():
        r = await client.post("/api/v1/environments", json={"template_id": t.json()["id"]})
        assert r.status_code == 201, r.text
        op = await lifecycle.claim(service.runtime, r.json()["id"], owner="test")
        assert op is not None and op.daytona_desktop_snapshot is not None
        return op

    return SimpleNamespace(service=service, cloud=cloud, create=create)


async def test_submit_poll_pin_and_reuse_before_allocation(setup, monkeypatch):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    opener = AsyncMock()
    monkeypatch.setattr(lifecycle, "open_provider", opener)
    result = await lifecycle.perform(runtime, op)
    assert result.pending and result.fault is None
    await lifecycle.publish(runtime, op, result)
    progress = await setup.service.client.get(f"/api/v1/environments/{op.environment_id}/creation")
    assert progress.json()["stage"] == "building"
    cloud.snapshot.state = "active"
    op = await lifecycle.claim(runtime, op.environment_id, owner="test")
    result = await lifecycle.perform(runtime, op)
    assert result.pending and result.fault is None
    opener.assert_not_called()
    await lifecycle.publish(runtime, op, result)
    async with transaction(runtime.storage) as session:
        row = await session.get(EnvironmentRow, op.environment_id)
        assert Handle.model_validate(row.handle).recipe["snapshot"] == "test-snapshot"
    other = await setup.create()
    assert other.daytona_desktop_snapshot == op.daytona_desktop_snapshot
    assert await builds.prepare(runtime, other.daytona_desktop_snapshot, other.target.account) == "test-snapshot"
    assert len([c for c in cloud.calls if c[0] == "create"]) == 1
    assert cloud.calls[-1] == ("find", "test-snapshot")


async def test_cancelled_submission_reconciles_without_duplicate_create(setup):
    runtime, cloud = setup.service.runtime, setup.cloud
    first, other = await setup.create(), await setup.create()
    cloud.gate = asyncio.Event()
    task = asyncio.create_task(builds.prepare(runtime, first.daytona_desktop_snapshot, first.target.account))
    await cloud.started.wait()
    assert await builds.prepare(runtime, other.daytona_desktop_snapshot, other.target.account) is None
    assert len(cloud.calls) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cloud.closed == 1
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.DaytonaDesktopSnapshotRow, first.daytona_desktop_snapshot)
        row.lease_expires_at = await now(session) - timedelta(seconds=1)
    cloud.snapshot.state = "active"
    assert await builds.prepare(runtime, first.daytona_desktop_snapshot, first.target.account) == "test-snapshot"
    assert len([c for c in cloud.calls if c[0] == "create"]) == 1


async def test_failed_snapshot_cleanup_reconciles_before_retry(setup):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    await builds.prepare(runtime, op.daytona_desktop_snapshot, op.target.account)
    cloud.snapshot.state = "build_failed"
    assert await builds.prepare(runtime, op.daytona_desktop_snapshot, op.target.account) is None
    cloud.delete_error = True
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(runtime, op.daytona_desktop_snapshot, op.target.account)
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.DaytonaDesktopSnapshotRow, op.daytona_desktop_snapshot)
        assert row.phase == "cleanup"
        row.lease_expires_at = await now(session) - timedelta(seconds=1)
    cloud.delete_error = False
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(runtime, op.daytona_desktop_snapshot, op.target.account)
    assert cloud.snapshot is None
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.DaytonaDesktopSnapshotRow, op.daytona_desktop_snapshot)
        assert row.phase == "failed"


async def test_unknown_submission_absence_never_resubmits(setup):
    op = await setup.create()
    runtime, cloud = setup.service.runtime, setup.cloud
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.DaytonaDesktopSnapshotRow, op.daytona_desktop_snapshot)
        row.phase = "submitting"
        row.deadline = await now(session) - timedelta(seconds=1)
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(runtime, op.daytona_desktop_snapshot, op.target.account)
    assert cloud.calls == [("find", None)]


async def test_credential_and_backend_changes_refuse_existing_preparation(setup):
    op = await setup.create()
    for account in [
        replace(op.target.account, credential=op.target.account.credential.model_copy(update={"nonce": "changed"})),
        replace(op.target.account, config={"target": "eu"}),
    ]:
        with pytest.raises(EnvironmentProviderError):
            await builds.prepare(setup.service.runtime, op.daytona_desktop_snapshot, account)
    assert not setup.cloud.calls


async def test_failed_build_preserved_until_fenced_retry(setup):
    runtime, client = setup.service.runtime, setup.service.client
    op = await setup.create()
    setup.cloud.error = failure("daytona", "desktop_snapshot_build_failed", Category.CONFLICT)
    result = await lifecycle.perform(runtime, op)
    assert result.fault is not None and result.fault.certainty == "not_dispatched"
    await lifecycle.publish(runtime, op, result)
    assert await lifecycle.claim(runtime, op.environment_id, owner="maintenance") is None
    progress = await client.get(f"/api/v1/environments/{op.environment_id}/creation")
    assert progress.json()["stage"] == "failed" and progress.json()["retryable"]
    current = await client.get(f"/api/v1/environments/{op.environment_id}")
    retried = await client.post(
        f"/api/v1/environments/{op.environment_id}/retry-creation", headers={"If-Match": current.headers["etag"]}
    )
    assert retried.status_code == 202, retried.text
    next_op = await lifecycle.claim(runtime, op.environment_id, owner="test")
    assert next_op.daytona_desktop_snapshot != op.daytona_desktop_snapshot
    assert next_op.generation > op.generation
    setup.cloud.error = None
    assert (await lifecycle.perform(runtime, next_op)).pending


async def test_stale_completion_cannot_pin_or_allocate(setup, monkeypatch):
    op = await setup.create()
    async with transaction(setup.service.runtime.storage) as session:
        row = await session.get(EnvironmentRow, op.environment_id)
        row.generation += 1
    opener = AsyncMock()
    monkeypatch.setattr(lifecycle, "open_provider", opener)
    await builds.prepare(setup.service.runtime, op.daytona_desktop_snapshot, op.target.account)
    setup.cloud.snapshot.state = "active"
    result = await lifecycle.perform(setup.service.runtime, op)
    assert result.fault is not None
    opener.assert_not_called()


async def test_deleting_unbuilt_environment_does_not_allocate(setup, monkeypatch):
    op = await setup.create()
    opener = AsyncMock()
    monkeypatch.setattr(lifecycle, "open_provider", opener)
    result = await lifecycle.perform(setup.service.runtime, replace(op, phase="deleting"))
    assert result.state is None and result.fault is None
    assert not setup.cloud.calls
    opener.assert_not_called()


async def test_expired_preparation_fails_before_cloud_work(setup):
    op = await setup.create()
    async with transaction(setup.service.runtime.storage) as session:
        row = await session.get(builds.DaytonaDesktopSnapshotRow, op.daytona_desktop_snapshot)
        row.deadline = await now(session) - timedelta(seconds=1)
    assert await builds.prepare(setup.service.runtime, op.daytona_desktop_snapshot, op.target.account) is None
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(setup.service.runtime, op.daytona_desktop_snapshot, op.target.account)
    assert not setup.cloud.calls


async def test_provider_and_manifest_revision_isolate_reusable_snapshots(setup, monkeypatch):
    op = await setup.create()
    capability = builds._capability(setup.service.runtime)
    monkeypatch.setattr(builds, "_capability", lambda _: replace(capability, revision=lambda: "next-release"))
    other = await setup.create()
    assert other.daytona_desktop_snapshot != op.daytona_desktop_snapshot
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(
            setup.service.runtime, op.daytona_desktop_snapshot, replace(op.target.account, id="another-provider")
        )
    assert not setup.cloud.calls
