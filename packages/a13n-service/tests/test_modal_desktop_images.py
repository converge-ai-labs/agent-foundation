"""Automatic Modal desktop image preparation and durable creation feedback."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentState
from a13n_harness.providers.environment.native.errors import failure
from a13n_service.infra.db import now, transaction
from a13n_service.runs.environments import lifecycle
from a13n_service.runs.environments import modal_images as builds
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow

pytestmark = pytest.mark.anyio


class Cloud:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.error = None
        self.gate = None
        self.started = asyncio.Event()

    async def build(self, manifest, scope, image_id):
        self.calls.append((manifest, scope, image_id))
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return "im-TestImage"

    async def close(self):
        self.closed += 1


@pytest.fixture
async def setup(service, monkeypatch):
    cloud = Cloud()
    monkeypatch.setattr(builds, "_builder", lambda *_: cloud)
    client = service.client
    p = await client.post(
        "/api/v1/environment-providers",
        json={
            "type": "modal",
            "name": "Modal",
            "credential": {"token_id": "test-id", "token_secret": "test-secret"},
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
        assert op is not None and op.modal_desktop_image is not None
        return op

    return SimpleNamespace(service=service, cloud=cloud, create=create)


async def test_first_build_then_reuse_pins_exact_image_before_allocation(setup, monkeypatch):
    runtime, cloud = setup.service.runtime, setup.cloud
    op = await setup.create()
    provider = AsyncMock()
    provider.create.return_value = EnvironmentState(provider_key="modal", state_version="1", state={})
    opener = AsyncMock(return_value=provider)
    monkeypatch.setattr(lifecycle, "open_provider", opener)
    result = await lifecycle.perform(runtime, op)
    assert result.pending and result.fault is None
    opener.assert_not_called()
    await lifecycle.publish(runtime, op, result)
    async with transaction(runtime.storage) as session:
        row = await session.get(EnvironmentRow, op.environment_id)
        assert Handle.model_validate(row.handle).recipe["image"] == "im-TestImage"
    allocation = await lifecycle.claim(runtime, op.environment_id, owner="test")
    result = await lifecycle.perform(runtime, allocation)
    assert result.fault is None
    assert provider.create.call_args.args[0]["image"] == "im-TestImage"
    other = await setup.create()
    assert other.modal_desktop_image == op.modal_desktop_image
    assert await builds.prepare(runtime, other.modal_desktop_image, other.target.account) == "im-TestImage"
    assert [c[2] for c in cloud.calls] == [None, "im-TestImage"]
    assert cloud.closed == 2


async def test_concurrent_preparation_serializes_and_cancel_closes_client(setup):
    runtime, cloud = setup.service.runtime, setup.cloud
    first, other = await setup.create(), await setup.create()
    cloud.gate = asyncio.Event()
    task = asyncio.create_task(builds.prepare(runtime, first.modal_desktop_image, first.target.account))
    await cloud.started.wait()
    assert await builds.prepare(runtime, other.modal_desktop_image, other.target.account) is None
    assert len(cloud.calls) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cloud.closed == 1
    # A replacement worker only rejoins after the previous lease has expired.
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.ModalDesktopImageRow, first.modal_desktop_image)
        row.lease_expires_at = await now(session) - timedelta(seconds=1)
    cloud.gate = None
    assert await builds.prepare(runtime, first.modal_desktop_image, first.target.account) == "im-TestImage"
    assert cloud.calls[0] == cloud.calls[1]


async def test_timeout_is_pending_with_same_manifest_and_scope(setup, monkeypatch):
    op = await setup.create()
    cloud = setup.cloud
    cloud.gate = asyncio.Event()
    monkeypatch.setattr(builds, "BUILD_SECONDS", 0.01)
    assert await builds.prepare(setup.service.runtime, op.modal_desktop_image, op.target.account) is None
    assert cloud.closed == 1
    cloud.gate = None
    assert await builds.prepare(setup.service.runtime, op.modal_desktop_image, op.target.account) == "im-TestImage"
    assert cloud.calls[0] == cloud.calls[1]


async def test_credential_and_backend_changes_refuse_existing_preparation(setup):
    op = await setup.create()
    for account in [
        replace(op.target.account, credential=op.target.account.credential.model_copy(update={"nonce": "changed"})),
        replace(op.target.account, config={"app_name": "other"}),
    ]:
        with pytest.raises(EnvironmentProviderError):
            await builds.prepare(setup.service.runtime, op.modal_desktop_image, account)
    assert not setup.cloud.calls


async def test_failed_build_preserved_until_fenced_retry(setup):
    runtime, client = setup.service.runtime, setup.service.client
    op = await setup.create()
    setup.cloud.error = failure("modal", "desktop_image_build_failed", Category.CONFLICT)
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
    assert next_op.modal_desktop_image != op.modal_desktop_image
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
        row = await session.get(builds.ModalDesktopImageRow, op.modal_desktop_image)
        row.deadline = await now(session) - timedelta(seconds=1)
    assert await builds.prepare(setup.service.runtime, op.modal_desktop_image, op.target.account) is None
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(setup.service.runtime, op.modal_desktop_image, op.target.account)
    assert not setup.cloud.calls


async def test_provider_and_manifest_revision_isolate_reusable_images(setup, monkeypatch):
    op = await setup.create()
    capability = builds._capability(setup.service.runtime)
    monkeypatch.setattr(builds, "_capability", lambda _: replace(capability, revision=lambda: "next-release"))
    other = await setup.create()
    assert other.modal_desktop_image != op.modal_desktop_image
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(
            setup.service.runtime, op.modal_desktop_image, replace(op.target.account, id="another-provider")
        )
    assert not setup.cloud.calls
