"""Automatic E2B preparation: durable retries, isolation and fixed allocation recipes."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentState
from a13n_service.infra.db import now, transaction
from a13n_service.providers.environment.e2b_build import Build, BuildStatus, Template
from a13n_service.providers.environments.e2b.configuration import E2BRecipe
from a13n_service.runs.environments import e2b_templates as builds
from a13n_service.runs.environments import lifecycle, maintenance
from a13n_service.runs.environments.schemas import Handle
from a13n_service.runs.environments.tables import EnvironmentRow

pytestmark = pytest.mark.anyio


class Cloud:
    def __init__(self):
        self.reserve_count = self.submit_count = self.delete_count = self.closed = 0
        self.status_value = "building"
        self.lose_reserve = self.lose_submit = False
        self.name = ""
        self.gate = None
        self.started = asyncio.Event()
        self.build = Build(templateID="template-test", buildID="build-test")

    async def reserve(self, name):
        self.reserve_count += 1
        self.name = name
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.lose_reserve:
            raise TimeoutError()
        return self.build

    async def find(self, name):
        assert name == self.name
        return await self.inspect(self.build.template_id, name)

    async def inspect(self, template_id, name):
        assert name == self.name and template_id == self.build.template_id
        return Template(
            templateID=template_id,
            public=False,
            names=[name],
            builds=[{"buildID": self.build.build_id, "status": self.status_value}],
        )

    async def submit(self, build, spec):
        self.submit_count += 1
        assert build == self.build and "fromImage" in spec
        if self.lose_submit:
            raise TimeoutError()

    async def status(self, build):
        return BuildStatus(**build.model_dump(by_alias=True), status=self.status_value)

    async def delete(self, template_id, name):
        assert template_id == self.build.template_id and name == self.name
        self.delete_count += 1

    async def close(self):
        self.closed += 1


@pytest.fixture
async def setup(service, monkeypatch):
    # These tests advance lifecycle operations explicitly; the control sweep must not race their claims or ETags.
    monkeypatch.setattr(maintenance, "advance", AsyncMock())
    cloud = Cloud()
    monkeypatch.setattr(builds, "_builder", lambda *_: cloud)
    client = service.client
    p = await client.post(
        "/api/v1/environment-providers",
        json={"type": "e2b", "name": "E2B", "credential": {"api_key": "test-key"}, "config": {}},
    )
    assert p.status_code == 201, p.text
    t = await client.post(
        "/api/v1/environment-templates",
        json={"name": "Desktop", "provider_id": p.json()["id"], "config": {"recipe": {"desktop": True}}},
    )
    assert t.status_code == 201, t.text

    async def create():
        r = await client.post("/api/v1/environments", json={"template_id": t.json()["id"]})
        assert r.status_code == 201, r.text
        op = await lifecycle.claim(service.runtime, r.json()["id"], owner="test")
        assert op is not None and op.e2b_desktop_template is not None
        return op

    return SimpleNamespace(service=service, cloud=cloud, create=create)


async def expire(runtime, key):
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.E2BDesktopTemplateRow, key)
        row.lease_expires_at = await now(session) - timedelta(seconds=1)


async def test_first_build_and_reuse_keep_instance_recipe_pinned(setup, monkeypatch):
    service, cloud = setup.service, setup.cloud
    op = await setup.create()
    key = op.e2b_desktop_template
    for _ in range(3):
        result = await lifecycle.perform(service.runtime, op)
        assert result.pending and result.fault is None
    cloud.status_value = "ready"
    provider = AsyncMock()
    provider.create.return_value = EnvironmentState(
        provider_key="e2b", state_version="1", state={"sandbox_id": "sandbox-test"}
    )
    monkeypatch.setattr(lifecycle, "open_provider", AsyncMock(return_value=provider))
    result = await lifecycle.perform(service.runtime, op)
    assert result.fault is None and not result.pending
    await lifecycle.publish(service.runtime, op, result)
    async with transaction(service.runtime.storage) as session:
        row = await session.get(EnvironmentRow, op.environment_id)
        assert row.status == "ready"
        handle = Handle.model_validate(row.handle)
        assert handle.recipe["template"] == "template-test"
        assert handle.e2b_desktop_template == key
    assert provider.create.call_args.args[0]["template"] == "template-test"
    other = await setup.create()
    assert other.e2b_desktop_template == key
    assert await builds.prepare(service.runtime, key, other.target.account) == "template-test"
    assert (cloud.reserve_count, cloud.submit_count) == (1, 1)
    # A later release gets another preparation; existing handles never float to the new revision.
    monkeypatch.setattr(builds, "_revision", lambda _: "next-release")
    latest = await setup.create()
    assert latest.e2b_desktop_template != key


async def test_concurrent_workers_reserve_once(setup):
    op = await setup.create()
    other = await setup.create()
    assert op.e2b_desktop_template == other.e2b_desktop_template
    cloud = setup.cloud
    cloud.gate = asyncio.Event()
    task = asyncio.create_task(builds.prepare(setup.service.runtime, op.e2b_desktop_template, op.target.account))
    await cloud.started.wait()
    assert await builds.prepare(setup.service.runtime, other.e2b_desktop_template, other.target.account) is None
    cloud.gate.set()
    await task
    assert cloud.reserve_count == 1


async def test_lost_reserve_and_submit_reconcile_without_replaying_mutations(setup):
    op = await setup.create()
    key, runtime, cloud = op.e2b_desktop_template, setup.service.runtime, setup.cloud
    cloud.lose_reserve = True
    with pytest.raises(TimeoutError):
        await builds.prepare(runtime, key, op.target.account)
    await expire(runtime, key)
    await builds.prepare(runtime, key, op.target.account)
    cloud.lose_submit = True
    with pytest.raises(TimeoutError):
        await builds.prepare(runtime, key, op.target.account)
    await expire(runtime, key)
    cloud.status_value = "ready"
    assert await builds.prepare(runtime, key, op.target.account) == "template-test"
    assert (cloud.reserve_count, cloud.submit_count) == (1, 1)
    assert cloud.closed == 4


async def test_credential_rotation_refuses_old_preparation_before_network(setup):
    op = await setup.create()
    account = op.target.account
    rotated = replace(account, credential=account.credential.model_copy(update={"nonce": "0123456789abcdef"}))
    with pytest.raises(EnvironmentProviderError) as error:
        await builds.prepare(setup.service.runtime, op.e2b_desktop_template, rotated)
    assert error.value.code == "desktop_build_credential_changed"
    assert setup.cloud.reserve_count == 0


async def test_failed_build_cleanup_and_delete_before_allocation(setup):
    op = await setup.create()
    runtime, key, cloud = setup.service.runtime, op.e2b_desktop_template, setup.cloud
    await builds.prepare(runtime, key, op.target.account)
    await builds.prepare(runtime, key, op.target.account)
    cloud.status_value = "error"
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(runtime, key, op.target.account)
    assert cloud.delete_count == 1
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(runtime, key, op.target.account)
    assert cloud.reserve_count == 1
    result = await lifecycle.perform(runtime, replace(op, phase="deleting"))
    assert result.state is None and result.fault is None and not result.pending


def test_auto_defaults_do_not_enable_access_for_existing_custom_templates():
    auto = E2BRecipe.model_validate({"desktop": True})
    assert (auto.template, auto.execution, auto.desktop_assistance) == ("auto", "envd_http", True)
    custom = E2BRecipe.model_validate({"desktop": True, "execution": "envd_http", "template": "my-desktop"})
    assert not custom.desktop_assistance


async def test_new_environment_retries_confirmed_failure_without_rebinding_old_one(setup):
    op = await setup.create()
    runtime, key, cloud = setup.service.runtime, op.e2b_desktop_template, setup.cloud
    await builds.prepare(runtime, key, op.target.account)
    await builds.prepare(runtime, key, op.target.account)
    cloud.status_value = "error"
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(runtime, key, op.target.account)
    retry = await setup.create()
    assert retry.e2b_desktop_template != key
    async with transaction(runtime.storage) as session:
        old = await session.get(EnvironmentRow, op.environment_id)
        assert Handle.model_validate(old.handle).e2b_desktop_template == key


async def test_delete_timeout_retains_cleanup_and_retries_only_exact_owned_template(setup, monkeypatch):
    op = await setup.create()
    runtime, key, cloud = setup.service.runtime, op.e2b_desktop_template, setup.cloud
    await builds.prepare(runtime, key, op.target.account)
    await builds.prepare(runtime, key, op.target.account)
    cloud.status_value = "error"
    original = cloud.delete
    monkeypatch.setattr(cloud, "delete", AsyncMock(side_effect=TimeoutError))
    with pytest.raises(TimeoutError):
        await builds.prepare(runtime, key, op.target.account)
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.E2BDesktopTemplateRow, key)
        assert row.phase == "cleanup" and row.template_id == cloud.build.template_id
    await expire(runtime, key)
    monkeypatch.setattr(cloud, "delete", original)
    with pytest.raises(EnvironmentProviderError):
        await builds.prepare(runtime, key, op.target.account)
    assert cloud.delete_count == 1 and cloud.reserve_count == 1


async def test_resolved_recipe_without_state_still_reconciles_native_deletion(setup, monkeypatch):
    op = await setup.create()
    provider = AsyncMock()
    monkeypatch.setattr(lifecycle, "open_provider", AsyncMock(return_value=provider))
    # A create response may be lost after the concrete template was pinned but before state publication.
    resolved = replace(
        op, phase="deleting", target=replace(op.target, recipe={**op.target.recipe, "template": "template-test"})
    )
    result = await lifecycle.perform(setup.service.runtime, resolved)
    assert result.fault is None
    provider.destroy.assert_awaited_once()
    assert setup.cloud.reserve_count == 0


@pytest.mark.parametrize(
    "change",
    [
        {"public": True},
        {"names": ["other-owner"]},
        {"templateID": "other-template"},
        {"builds": [{"buildID": "a", "status": "ready"}, {"buildID": "b", "status": "ready"}]},
    ],
)
async def test_cleanup_refuses_mismatched_or_shared_cloud_template(change):
    from a13n_service.providers.environments.e2b.desktop_build import DesktopBuildAPI

    api = DesktopBuildAPI("e2b.dev", "test-key")
    data = {
        "templateID": "template-test",
        "public": False,
        "names": ["owned-name"],
        "builds": [{"buildID": "build-test", "status": "ready"}],
        **change,
    }
    request = AsyncMock(return_value=data)
    api.http.request = request
    try:
        with pytest.raises(EnvironmentProviderError) as error:
            await api.delete("template-test", "owned-name")
        assert error.value.code == "desktop_template_identity_mismatch"
        assert [call.args[0] for call in request.call_args_list] == ["GET"]
    finally:
        await api.close()


async def test_interrupted_preparation_does_not_make_unallocated_environment_undeletable(setup):
    op = await setup.create()
    fault = lifecycle.Fault("environment_operation_interrupted", "Interrupted", "unknown")
    await lifecycle.publish(setup.service.runtime, replace(op, unresolved=True), lifecycle.Outcome(None, fault))
    async with transaction(setup.service.runtime.storage) as session:
        row = await session.get(EnvironmentRow, op.environment_id)
        assert row.failure["certainty"] == "not_dispatched"
        assert lifecycle.settled(row)


async def test_creation_progress_reports_cloud_facts_without_cloud_reads(setup):
    op = await setup.create()
    client, runtime = setup.service.client, setup.service.runtime
    url = f"/api/v1/environments/{op.environment_id}/creation"
    assert (await client.get(url)).json()["stage"] == "preparing"
    await builds.prepare(runtime, op.e2b_desktop_template, op.target.account)
    await builds.prepare(runtime, op.e2b_desktop_template, op.target.account)
    response = await client.get(url)
    assert response.json()["stage"] == "building"
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"stage", "started_at", "observed_at", "ended_at", "retryable"}
    assert setup.cloud.reserve_count == setup.cloud.submit_count == 1
    setup.cloud.status_value = "error"
    result = await lifecycle.perform(runtime, op)
    await lifecycle.publish(runtime, op, result)
    failed = (await client.get(url)).json()
    assert failed["stage"] == "failed" and failed["retryable"] and failed["ended_at"]
    response = await client.post(
        f"/api/v1/environments/{op.environment_id}/retry-creation",
        headers={"If-Match": (await client.get(f"/api/v1/environments/{op.environment_id}")).headers["etag"]},
    )
    assert response.status_code == 202, response.text
    async with transaction(runtime.storage) as session:
        row = await session.get(EnvironmentRow, op.environment_id)
        assert Handle.model_validate(row.handle).e2b_desktop_template != op.e2b_desktop_template
        assert row.failure is None and row.operation_id != op.operation_id
    assert (await client.get(url)).json()["stage"] == "preparing"
    # A double click cannot start another operation, and old completion cannot publish.
    assert (
        await client.post(
            f"/api/v1/environments/{op.environment_id}/retry-creation", headers={"If-Match": response.headers["etag"]}
        )
    ).status_code == 409
    await lifecycle.publish(runtime, op, result)
    assert (await client.get(url)).json()["stage"] == "preparing"


async def test_retry_refuses_active_or_unknown_creation_and_stale_etag(setup):
    op = await setup.create()
    client, runtime = setup.service.client, setup.service.runtime
    url = f"/api/v1/environments/{op.environment_id}/retry-creation"
    assert (
        await client.post(
            url, headers={"If-Match": (await client.get(f"/api/v1/environments/{op.environment_id}")).headers["etag"]}
        )
    ).status_code == 409
    async with transaction(runtime.storage) as session:
        row = await session.get(EnvironmentRow, op.environment_id)
        row.lease_owner = row.lease_token_hash = row.lease_expires_at = None
        lifecycle.record_failure(
            row, lifecycle.Fault("timeout", "Not confirmed", "unknown"), await now(session), unresolved=True
        )
    assert (
        await client.post(
            url, headers={"If-Match": (await client.get(f"/api/v1/environments/{op.environment_id}")).headers["etag"]}
        )
    ).status_code == 409
    assert (await client.post(url, headers={"If-Match": f'"{op.environment_id}:0"'})).status_code == 412


async def test_desktop_preview_rejects_creating_environment_without_provider_io(setup):
    op = await setup.create()
    response = await setup.service.client.get(f"/api/v1/environments/{op.environment_id}/desktop-preview")
    assert response.status_code == 409
    assert setup.cloud.reserve_count == 0


async def test_confirmed_failure_keeps_original_error_until_explicit_retry(setup):
    op = await setup.create()
    runtime = setup.service.runtime
    async with transaction(runtime.storage) as session:
        row = await session.get(builds.E2BDesktopTemplateRow, op.e2b_desktop_template)
        row.phase = "failed"
    fault = lifecycle.Fault("provider_authentication_failed", "Check E2B credentials", "not_dispatched", True)
    await lifecycle.publish(runtime, op, lifecycle.Outcome(None, fault))
    assert await lifecycle.claim(runtime, op.environment_id, owner="next") is None
    current = (await setup.service.client.get(f"/api/v1/environments/{op.environment_id}")).json()
    assert current["failure"]["code"] == "provider_authentication_failed"


@pytest.mark.parametrize("outcome", ["success", "timeout", "changed"])
async def test_preview_revalidates_target_and_closes_execution(setup, monkeypatch, outcome):
    from a13n_harness.environment.computer import ComputerObservation, ComputerScreenshot
    from a13n_service.runs.environments import desktop_preview

    op = await setup.create()
    runtime, client = setup.service.runtime, setup.service.client
    state = EnvironmentState(provider_key="e2b", state_version="1", state={"sandbox_id": "test"})
    await lifecycle.publish(runtime, op, lifecycle.Outcome(state))
    async with transaction(runtime.storage) as session:
        stamp = await now(session)
    screenshot = ComputerScreenshot(
        ComputerObservation(
            execution_id="exec-test",
            observed_generation="test",
            observation_id="obs-test",
            target_id="screen",
            width=1,
            height=1,
            mime_type="image/png",
            captured_at=stamp,
        ),
        b"test-image",
    )
    execution = AsyncMock()
    execution.descriptor.permissions.operations = {"environment.computer.observe"}

    async def observe():
        if outcome == "timeout":
            raise TimeoutError()
        if outcome == "changed":
            async with transaction(runtime.storage) as session:
                row = await session.get(EnvironmentRow, op.environment_id)
                await lifecycle.begin(session, row, "stopping")
        return screenshot

    execution.operations.computer.observe.side_effect = observe
    connector = AsyncMock()
    connector.open.return_value = execution
    monkeypatch.setattr(desktop_preview, "execution_connector", AsyncMock(return_value=connector))
    close = AsyncMock()
    monkeypatch.setattr(desktop_preview, "close", close)
    response = await client.get(f"/api/v1/environments/{op.environment_id}/desktop-preview")
    assert response.status_code == {"success": 200, "timeout": 503, "changed": 409}[outcome], response.text
    if outcome == "success":
        assert response.content == b"test-image"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-type"] == "image/png"
    close.assert_awaited_once_with(execution)


async def test_creation_queries_cannot_cross_workspace(setup):
    op = await setup.create()
    other = await setup.service.client.post(f"{setup.service.organization}/workspaces", json={"name": "Other"})
    assert other.status_code == 201
    for suffix in ("creation", "desktop-preview"):
        response = await setup.service.client.get(
            f"/api/v1/environments/{op.environment_id}/{suffix}", headers={"x-workspace-id": other.json()["id"]}
        )
        assert response.status_code in {403, 404}
    assert setup.cloud.reserve_count == 0
