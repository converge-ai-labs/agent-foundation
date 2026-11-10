"""Vercel preparation: reuse, lost outcomes, account isolation and confirmed cleanup."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.providers.environment.native.errors import failure
from a13n_service.infra.db import now, transaction
from a13n_service.providers.environment.vercel_build import BuildTarget, Snapshot
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.runs.environments import lifecycle, maintenance
from a13n_service.runs.environments import vercel_snapshots as builds
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow
from sqlalchemy import select

pytestmark = pytest.mark.anyio


class Cloud:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.target = None
        self.image = None
        self.error = None
        self.gate = None
        self.started = asyncio.Event()
        self.delete_error = False
        self.status = "ready"
        self.snapshot_error = False

    async def create(self, name, manifest):
        self.calls.append("create")
        self.started.set()
        if self.error:
            raise self.error
        self.target = BuildTarget(session_id="ses_1", created_at=1)
        if self.gate:
            await self.gate.wait()
        return self.target

    async def find_builder(self, *args):
        self.calls.append("find_builder")
        return self.target

    async def install(self, *args):
        self.calls.append("install")
        return self.status

    async def snapshot(self, *args):
        self.calls.append("snapshot")
        self.image = Snapshot(id="snap_test", state="created")
        if self.snapshot_error:
            raise TimeoutError
        return self.image

    async def find_snapshot(self, *args):
        self.calls.append("find_snapshot")
        return self.image

    async def delete_builder(self, *args):
        self.calls.append("delete_builder")
        if self.delete_error:
            raise failure("vercel", "cleanup_pending", Category.UNAVAILABLE)
        self.target = None

    async def delete_snapshot(self, *args):
        self.calls.append("delete_snapshot")
        self.image = None

    async def close(self):
        self.closed += 1


@pytest.fixture
async def setup(service, monkeypatch, runs_kit):
    await runs_kit.pause_sweeps(service)
    cloud = Cloud()
    monkeypatch.setattr(builds, "_builder", lambda *_: cloud)
    p = await service.client.post(
        "/api/v1/environment-providers",
        json={
            "type": "vercel",
            "name": "Vercel",
            "credential": {"api_key": "test-key"},
            "config": {"team_id": "team_a", "project_id": "prj_a"},
        },
    )
    assert p.status_code == 201, p.text
    t = await service.client.post(
        "/api/v1/environment-templates",
        json={
            "name": "Desktop",
            "provider_id": p.json()["id"],
            "config": {"recipe": {"desktop": True}},
        },
    )
    assert t.status_code == 201, t.text

    async def create(*, legacy=True):
        r = await service.client.post("/api/v1/environments", json={"template_id": t.json()["id"]})
        assert r.status_code == 201, r.text
        op = await lifecycle.claim(service.runtime, r.json()["id"], owner="test")
        assert op is not None and op.vercel_desktop_snapshot is None
        if legacy:
            # Seed a pre-upgrade handle: new automatic instances no longer bind snapshot jobs.
            async with transaction(service.runtime.storage) as session:
                row = await session.get(EnvironmentRow, op.environment_id)
                key = await builds.bind(service.runtime, session, row, op.target.account)
                handle = Handle.model_validate(row.handle)
                row.handle = handle.model_copy(update={"vercel_desktop_snapshot": key}).model_dump(mode="json")
            op = replace(op, vercel_desktop_snapshot=key)
        return op

    return SimpleNamespace(service=service, cloud=cloud, create=create)


async def expire(runtime, key):
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.VercelDesktopSnapshotRow, key)
        row.lease_expires_at = await now(session) - timedelta(seconds=1)


async def test_build_cleanup_pin_and_reuse(setup, monkeypatch):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    opener = AsyncMock()
    monkeypatch.setattr(lifecycle, "open_provider", opener)
    for _ in range(4):
        result = await lifecycle.perform(runtime, op)
        assert result.pending and result.fault is None
        await lifecycle.publish(runtime, op, result)
        progress = await setup.service.client.get(f"/api/v1/environments/{op.environment_id}/creation")
        assert progress.json()["stage"] in {"building", "starting"}
        async with transaction(runtime.storage) as session:
            row = await session.get(EnvironmentRow, op.environment_id)
            assert row.next_operation_at is not None
            row.next_operation_at = await now(session) - timedelta(seconds=1)
        op = await lifecycle.claim(runtime, op.environment_id, owner="test")
        assert op is not None
    opener.assert_not_called()
    async with transaction(runtime.storage) as session:
        row = await session.get(EnvironmentRow, op.environment_id)
        assert Handle.model_validate(row.handle).recipe["snapshot"] == "snap_test"
    assert cloud.target is None and cloud.image is not None
    other = await setup.create()
    assert other.vercel_desktop_snapshot == op.vercel_desktop_snapshot
    assert await builds.prepare(runtime, other.vercel_desktop_snapshot, other.target.account) == "snap_test"
    assert cloud.calls.count("create") == 1 and cloud.calls[-1] == "find_snapshot"


async def test_cancelled_allocation_reconciles_without_duplicate(setup):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    cloud.gate = asyncio.Event()
    task = asyncio.create_task(builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account))
    await cloud.started.wait()
    assert await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account) is None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cloud.closed == 1
    await expire(runtime, op.vercel_desktop_snapshot)
    await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    assert cloud.calls == ["create", "find_builder"]


async def test_lost_snapshot_response_is_found_not_resubmitted(setup):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    cloud.snapshot_error = True
    with pytest.raises(TimeoutError):
        await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    await expire(runtime, op.vercel_desktop_snapshot)
    for _ in range(2):
        await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    assert cloud.calls.count("snapshot") == 1 and cloud.target is None


async def test_failed_install_requires_confirmed_cleanup(setup):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    cloud.status = "failed"
    for _ in range(2):
        await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    cloud.delete_error = True
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.VercelDesktopSnapshotRow, op.vercel_desktop_snapshot)
        assert row.phase == "cleanup"
    await expire(runtime, op.vercel_desktop_snapshot)
    cloud.delete_error = False
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    assert cloud.target is None and "snapshot" not in cloud.calls


async def test_failed_build_requires_explicit_fenced_retry(setup):
    runtime, client = setup.service.runtime, setup.service.client
    op = await setup.create()
    setup.cloud.error = failure("vercel", "provider_denied", Category.DENIED, certainty=Certainty.KNOWN)
    result = await lifecycle.perform(runtime, op)
    await lifecycle.publish(runtime, op, result)
    assert await lifecycle.claim(runtime, op.environment_id, owner="maintenance") is None
    progress = await client.get(f"/api/v1/environments/{op.environment_id}/creation")
    assert progress.json()["stage"] == "failed" and progress.json()["retryable"]
    current = await client.get(f"/api/v1/environments/{op.environment_id}")
    response = await client.post(
        f"/api/v1/environments/{op.environment_id}/retry-creation", headers={"If-Match": current.headers["etag"]}
    )
    assert response.status_code == 202, response.text
    following = await lifecycle.claim(runtime, op.environment_id, owner="test")
    assert following.vercel_desktop_snapshot is None and following.generation > op.generation
    async with transaction(runtime.storage) as session:
        assert list(await session.scalars(select(builds.VercelDesktopSnapshotRow.id))) == [op.vercel_desktop_snapshot]


@pytest.mark.parametrize("phase", ["allocating", "snapshotting"])
async def test_expired_unknown_outcomes_never_resubmit(setup, phase):
    runtime = setup.service.runtime
    op = await setup.create()
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.VercelDesktopSnapshotRow, op.vercel_desktop_snapshot)
        row.phase = phase
        row.builder = {"session_id": "ses_1", "created_at": 1} if phase == "snapshotting" else None
        row.deadline = await now(session) - timedelta(seconds=1)
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    assert all(c.startswith("find") for c in setup.cloud.calls)


async def test_credentials_team_project_and_provider_are_isolated(setup):
    op = await setup.create()
    for account in [
        replace(op.target.account, credential=op.target.account.credential.model_copy(update={"nonce": "changed"})),
        replace(op.target.account, config={"team_id": "team_b", "project_id": "prj_a"}),
        replace(op.target.account, config={"team_id": "team_a", "project_id": "prj_b"}),
        replace(op.target.account, id="other-provider"),
    ]:
        with pytest.raises(EnvironmentProviderError):
            await builds.prepare(setup.service.runtime, op.vercel_desktop_snapshot, account)
    assert not setup.cloud.calls


async def test_delete_mid_install_cleans_builder_without_snapshot(setup):
    runtime = setup.service.runtime
    op = await setup.create()
    await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    for _ in range(3):
        done = await builds.cancel(runtime, op.vercel_desktop_snapshot, op.target.account, op.environment_id)
    assert done and setup.cloud.target is None and "snapshot" not in setup.cloud.calls


async def test_delete_keeps_builder_needed_by_another_instance(setup):
    op = await setup.create()
    other = await setup.create()
    await builds.prepare(setup.service.runtime, op.vercel_desktop_snapshot, op.target.account)
    assert await builds.cancel(setup.service.runtime, op.vercel_desktop_snapshot, op.target.account, op.environment_id)
    assert other.vercel_desktop_snapshot == op.vercel_desktop_snapshot and setup.cloud.target is not None


async def test_stale_build_completion_cannot_replace_current_lease(setup):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    cloud.gate = asyncio.Event()
    task = asyncio.create_task(builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account))
    await cloud.started.wait()
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.VercelDesktopSnapshotRow, op.vercel_desktop_snapshot)
        row.lease = "new-owner"
    cloud.gate.set()
    assert await task is None
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.VercelDesktopSnapshotRow, op.vercel_desktop_snapshot)
        assert row.lease == "new-owner" and row.phase == "allocating" and row.builder is None
    assert cloud.closed == 1


async def test_failed_cached_snapshot_requires_cleanup_before_rebuild(setup):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    for _ in range(3):
        await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    cloud.image = Snapshot(id="snap_test", state="failed")
    assert await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account) is None
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    assert cloud.image is None and cloud.calls[-2:] == ["delete_snapshot", "delete_builder"]


async def test_new_automatic_targets_skip_snapshot_build_and_checkpoint_pending(setup, monkeypatch):
    from a13n_harness.environment.models import EnvironmentState
    from a13n_service.providers.environment.management import Pending

    runtime = setup.service.runtime
    op = await setup.create(legacy=False)
    assert op.vercel_desktop_snapshot is None
    assert op.target.recipe["snapshot"] == "auto"
    state = EnvironmentState(provider_key="vercel", state_version="1", state={"target_id": "owned-final"})
    provider = AsyncMock()
    provider.configure_envd_version = Mock()
    provider.create.return_value = Pending(state, retry_after_seconds=3, settled=True)
    monkeypatch.setattr(lifecycle, "open_provider", AsyncMock(return_value=provider))
    result = await lifecycle.perform(runtime, op)
    assert result.pending and result.settled and result.state == state
    assert result.retry_after_seconds == 3 and result.fault is None
    await lifecycle.publish(runtime, op, result)
    async with transaction(runtime.storage) as session:
        row = await session.get(EnvironmentRow, op.environment_id)
        handle = Handle.model_validate(row.handle)
        assert handle.state == state and handle.vercel_desktop_snapshot is None
        assert row.next_operation_at is not None and row.failure is None
        assert row.status == "creating" and row.operation_id == op.operation_id
    progress = await setup.service.client.get(f"/api/v1/environments/{op.environment_id}/creation")
    assert progress.status_code == 200 and progress.json()["stage"] == "building"
    assert not setup.cloud.calls
    provider.close.assert_awaited_once()


async def abandon(runtime, op):
    async with transaction(runtime.storage) as session:
        row = await session.get(EnvironmentRow, op.environment_id)
        row.status = "deleted"
        row.handle = None
        row.operation_id = row.operation_started_at = row.operation_deadline = None
        row.lease_owner = row.lease_token_hash = row.lease_expires_at = None
        row.next_operation_at = None
        row.operation_unsettled = False


async def test_maintenance_drains_orphan_builder_with_disabled_provider(setup):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    await abandon(runtime, op)
    async with transaction(runtime.storage) as session:
        provider = await session.get(EnvironmentProviderRow, op.target.account.id)
        provider.enabled = False
    for _ in range(2):
        await maintenance.maintain_environments(runtime, owner="test-drain")
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.VercelDesktopSnapshotRow, op.vercel_desktop_snapshot)
        assert row.phase == "failed" and row.builder is not None
    assert cloud.target is None and cloud.calls == ["create", "delete_builder"]


@pytest.mark.parametrize("terminal", ["new", "ready", "failed"])
async def test_orphan_drain_never_allocates_or_deletes_ready_assets(setup, terminal):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.VercelDesktopSnapshotRow, op.vercel_desktop_snapshot)
        row.phase = terminal
        row.snapshot_id = "snap_keep" if terminal == "ready" else None
    await abandon(runtime, op)
    await builds.drain(runtime, op.vercel_desktop_snapshot)
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.VercelDesktopSnapshotRow, op.vercel_desktop_snapshot)
        assert row.phase == ("failed" if terminal == "new" else terminal)
        assert row.snapshot_id == ("snap_keep" if terminal == "ready" else None)
    assert not cloud.calls


async def test_orphan_claim_rechecks_waiters_and_active_leases(setup):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    # Even a cancellation submitted from a stale scan cannot take a live waiter's builder.
    await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account, cancel=True)
    assert cloud.calls == ["create"]
    await abandon(runtime, op)
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.VercelDesktopSnapshotRow, op.vercel_desktop_snapshot)
        row.lease = "live-owner"
        row.lease_expires_at = await now(session) + timedelta(minutes=5)
    await builds.drain(runtime, op.vercel_desktop_snapshot)
    assert cloud.calls == ["create"]
    await expire(runtime, op.vercel_desktop_snapshot)
    await builds.drain(runtime, op.vercel_desktop_snapshot)
    await builds.drain(runtime, op.vercel_desktop_snapshot)
    assert cloud.calls == ["create", "delete_builder"]


async def test_orphan_lost_snapshot_response_reconciles_before_cleanup(setup):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    cloud.snapshot_error = True
    with pytest.raises(TimeoutError):
        await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    await abandon(runtime, op)
    await expire(runtime, op.vercel_desktop_snapshot)
    for _ in range(2):
        await builds.drain(runtime, op.vercel_desktop_snapshot)
    assert cloud.calls == ["create", "install", "snapshot", "find_snapshot", "delete_snapshot", "delete_builder"]
    assert cloud.target is None and cloud.image is None


async def test_orphan_cleanup_failure_retains_identity(setup):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    await builds.prepare(runtime, op.vercel_desktop_snapshot, op.target.account)
    await abandon(runtime, op)
    await builds.drain(runtime, op.vercel_desktop_snapshot)
    cloud.delete_error = True
    with pytest.raises(EnvironmentProviderError):
        await builds.drain(runtime, op.vercel_desktop_snapshot)
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.VercelDesktopSnapshotRow, op.vercel_desktop_snapshot)
        assert row.phase == "cleanup" and row.builder["session_id"] == "ses_1"
    await expire(runtime, op.vercel_desktop_snapshot)
    cloud.delete_error = False
    await builds.drain(runtime, op.vercel_desktop_snapshot)
    assert cloud.target is None and cloud.calls.count("create") == 1


@pytest.mark.parametrize("change", ["credential", "backend"])
async def test_orphan_changed_account_is_retained_and_rotates_behind_other_work(setup, change):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    await abandon(runtime, op)
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.VercelDesktopSnapshotRow, op.vercel_desktop_snapshot)
        if change == "credential":
            row.credential_version = "old-credential"
        else:
            row.backend = "old-account"
        row.scope = "retired-scope"
    other = await setup.create()
    await abandon(runtime, other)
    with pytest.raises(EnvironmentProviderError):
        await builds.drain(runtime, op.vercel_desktop_snapshot)
    async with transaction(runtime.storage) as session:
        candidates = list(await session.scalars(builds.orphaned().limit(1)))
        assert candidates == [other.vercel_desktop_snapshot]
        old = await session.get(builds.VercelDesktopSnapshotRow, op.vercel_desktop_snapshot)
        assert old.phase == "new"
    assert not cloud.calls


@pytest.mark.parametrize("phase", ["creating", "starting"])
@pytest.mark.parametrize("action", ["retry", "delete"])
async def test_authored_startup_lost_reply_then_failure_allows_explicit_recovery(
    setup, monkeypatch, tmp_path, phase, action
):
    from a13n_harness.providers.environment.vercel.shared import SandboxResponse
    from a13n_service.providers.environments.vercel import management as module

    service = setup.service
    marker = tmp_path / "repaired"
    effects = tmp_path / "effects"
    template = await service.client.post(
        "/api/v1/environment-templates",
        json={
            "name": "Authored startup",
            "provider_id": (await service.client.get("/api/v1/environment-providers")).json()["items"][0]["id"],
            "config": {
                "recipe": {
                    "execution": "envd_http",
                    "snapshot": "snap_team",
                    "envd_management": "preinstalled",
                    "startup_command": ["sh", "-c", f"printf x >> {effects}; test -f {marker}"],
                }
            },
        },
    )
    assert template.status_code == 201, template.text
    created = await service.client.post("/api/v1/environments", json={"template_id": template.json()["id"]})
    assert created.status_code == 201, created.text
    identity = created.json()["id"]
    url = f"/api/v1/environments/{identity}"
    lost = True

    async def lookup(self):
        result = SandboxResponse.model_validate(
            {
                "sandbox": {
                    "name": self.name,
                    "createdAt": 1,
                    "persistent": True,
                    "status": "running",
                    "tags": self.labels,
                },
                "session": {"id": "session-authored", "status": "running", "timeout": 3600000},
                "routes": [{"port": 8888, "url": "https://envd.vercel.run"}],
            }
        )
        self.accept(result)
        return result

    async def command(ref, session_id, argv, **kwargs):
        nonlocal lost
        script = argv[2].replace("/var/lib/a13n-startup/", str(tmp_path / "jobs") + "/")
        process = await asyncio.create_subprocess_exec("sh", "-c", script, stdout=asyncio.subprocess.PIPE)
        output, _ = await process.communicate()
        assert process.returncode == 0
        if lost:
            lost = False
            async with asyncio.timeout(5):
                while not any(path.read_text() == "failed" for path in (tmp_path / "jobs").glob("*/status")):
                    await asyncio.sleep(0.01)
            raise TimeoutError("lost startup reply")
        return output.decode()

    monkeypatch.setattr(module.VercelManagement, "lookup", lookup)
    monkeypatch.setattr(module, "command", command)
    monkeypatch.setattr(module, "wait_until_ready", AsyncMock())
    if phase == "starting":
        # Establish the previously ready target before exercising its resume startup.
        monkeypatch.setattr(module, "command", AsyncMock(return_value="ready"))
        await lifecycle.advance(service.runtime, identity, owner="initial")
        monkeypatch.setattr(module, "command", command)
        async with transaction(service.runtime.storage) as session:
            row = await session.get_one(EnvironmentRow, identity, with_for_update=True)
            assert row.status == "ready"
            await lifecycle.begin(session, row, "starting")
    first = await lifecycle.claim(service.runtime, identity, owner="lost")
    await lifecycle.publish(service.runtime, first, await lifecycle.perform(service.runtime, first))
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, identity, with_for_update=True)
        assert not lifecycle.settled(row) and Handle.model_validate(row.handle).state is not None
        row.next_operation_at = None
    second = await lifecycle.claim(service.runtime, identity, owner="reconcile")
    assert second.operation_id == first.operation_id
    await lifecycle.publish(service.runtime, second, await lifecycle.perform(service.runtime, second))
    async with transaction(service.runtime.storage) as session:
        row = await session.get_one(EnvironmentRow, identity, with_for_update=True)
        assert lifecycle.settled(row) and row.failure["code"] == "envd_startup_failed"
        assert row.failure["permanent"] and row.failure["certainty"] == "known"
        before = Handle.model_validate(row.handle)
        row.next_operation_at = None
    assert await lifecycle.claim(service.runtime, identity, owner="no-replay") is None
    assert effects.read_text() == "x"
    progress = await service.client.get(url + "/creation")
    assert progress.json()["stage"] == "failed" and progress.json()["retryable"]
    current = await service.client.get(url)
    if action == "delete":
        deleted = await service.client.delete(url, headers={"If-Match": current.headers["etag"]})
        assert deleted.status_code == 202, deleted.text
        return
    marker.touch()
    retried = await service.client.post(url + "/retry-creation", headers={"If-Match": current.headers["etag"]})
    assert retried.status_code == 202, retried.text
    async with asyncio.timeout(5):
        while True:
            operation = await lifecycle.claim(service.runtime, identity, owner="retry")
            assert operation is not None and operation.operation_id != first.operation_id
            assert operation.target.state == before.state and operation.target.recipe == before.recipe
            await lifecycle.publish(service.runtime, operation, await lifecycle.perform(service.runtime, operation))
            async with transaction(service.runtime.storage) as session:
                row = await session.get_one(EnvironmentRow, identity, with_for_update=True)
                if row.status == "ready":
                    break
                row.next_operation_at = None
            await asyncio.sleep(0.01)
    assert effects.read_text() == "xx"
