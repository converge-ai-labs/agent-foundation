"""Automatic defaults are atomic, opt-in and relinquished to the user after initialization."""

import asyncio
from unittest.mock import AsyncMock

import httpx2
import pytest
from a13n_service.app import build_app
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session, transaction
from a13n_service.provisioning import docker
from a13n_service.provisioning.service import Initializer
from a13n_service.provisioning.tables import WorkspaceProvisioningRow
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
from a13n_service.resources.providers.probe import ProbeResult
from a13n_service.resources.providers.tables import EnvironmentProviderRow
from a13n_service.settings import Provisioning, Settings, load_settings
from a13n_service.tenancy.tables import WorkspaceRow
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update

pytestmark = pytest.mark.anyio


@pytest.fixture
def settings(settings, tmp_path):
    return settings.model_copy(
        update={
            "provisioning": Provisioning.model_validate(
                {
                    "local": {"enabled": True, "root": str(tmp_path / "environments")},
                    "docker": {"enabled": True},
                }
            )
        }
    )


@pytest.fixture
def ping(monkeypatch):
    ping = AsyncMock(return_value=ProbeResult("succeeded"))
    monkeypatch.setattr(docker, "probe", ping)
    monkeypatch.setattr("a13n_service.provisioning.service.RETRY_DELAYS", (0, 0))
    return ping


@pytest.fixture
def initializer(runtime, ping):
    return Initializer(
        runtime.storage,
        runtime.registry,
        runtime.keys,
        runtime.tasks,
        runtime.settings.provisioning,
        runtime.endpoint_policy,
    )


async def receipts(runtime, workspace_id):
    async with short_session(runtime.storage) as session:
        return list(
            await session.scalars(
                select(WorkspaceProvisioningRow)
                .where(WorkspaceProvisioningRow.workspace_id == workspace_id)
                .order_by(WorkspaceProvisioningRow.component)
            )
        )


async def test_defaults_are_off_and_local_requires_an_explicit_absolute_root():
    config = Settings()
    assert not config.provisioning.local.enabled and not config.provisioning.docker.enabled
    for root in (None, "relative", "/tmp/../elsewhere"):
        with pytest.raises(ValidationError):
            Provisioning.model_validate({"local": {"enabled": True, "root": root}})
    with pytest.raises(ValidationError):
        Settings.model_validate({"environments": {"allow_local": True}})


async def test_concurrent_initialization_creates_one_atomic_pair_per_component(runtime, tenant, initializer):
    await asyncio.gather(*(initializer(tenant.workspace_id) for _ in range(5)))
    rows = await receipts(runtime, tenant.workspace_id)
    assert [row.component for row in rows] == ["docker", "local"]
    async with short_session(runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(EnvironmentProviderRow)) == 2
        assert await session.scalar(select(func.count()).select_from(EnvironmentTemplateRow)) == 2
        for receipt in rows:
            provider = await session.get_one(EnvironmentProviderRow, receipt.provider_id)
            template = await session.get_one(EnvironmentTemplateRow, receipt.template_id)
            assert provider.created_by_id is None and provider.updated_by_id is None
            assert template.created_by_id is None and template.updated_by_id is None
            assert provider.config == {} and template.provider_id == provider.id
            if receipt.component == "docker":
                assert template.config["recipe"] == {"image": "a13n-docker-environment:local", "pull_policy": "never"}
            else:
                assert template.config["recipe"] == {
                    "root": {"path": str(runtime.settings.provisioning.local.root)},
                    "shell_profiles": [{"profile_id": "sh", "executable": "/bin/sh"}],
                }
        audit = list(await session.scalars(select(AuditEventRow).where(AuditEventRow.actor_id.is_(None))))
        assert len(audit) == 4
    assert not runtime.settings.provisioning.local.root.exists()


async def test_user_edits_disabling_and_deletion_survive_startup(service, initializer, ping):
    runtime, client, workspace_id = service.runtime, service.client, service.tenant.workspace_id
    await initializer(workspace_id)
    docker_receipt, local_receipt = await receipts(runtime, workspace_id)
    for collection, resource_id, name in (
        ("environment-providers", local_receipt.provider_id, "My Local"),
        ("environment-templates", local_receipt.template_id, "My workspace"),
    ):
        path = f"/api/v1/{collection}/{resource_id}"
        current = await client.get(path)
        response = await client.patch(
            path, json={"name": name, "enabled": False}, headers={"if-match": current.headers["etag"]}
        )
        assert response.status_code == 200, response.text
        assert response.json()["created_by_id"] is None
        assert response.json()["updated_by_id"] == service.tenant.principal_id
    # These resources currently expose disabling through the API, not deletion. Removing their rows must
    # still leave the historical completion fact intact rather than blocking deletion or recreating them.
    async with transaction(runtime.storage) as session:
        await session.execute(
            delete(EnvironmentTemplateRow).where(EnvironmentTemplateRow.id == docker_receipt.template_id)
        )
        await session.execute(
            delete(EnvironmentProviderRow).where(EnvironmentProviderRow.id == docker_receipt.provider_id)
        )
    ping.reset_mock()
    await initializer.existing()
    ping.assert_not_called()
    async with short_session(runtime.storage) as session:
        assert await session.get(EnvironmentProviderRow, docker_receipt.provider_id) is None
        local = await session.get_one(EnvironmentProviderRow, local_receipt.provider_id)
        assert local.name == "My Local" and not local.enabled
        template = await session.get_one(EnvironmentTemplateRow, local_receipt.template_id)
        assert template.name == "My workspace" and not template.enabled
    assert len(await receipts(runtime, workspace_id)) == 2


async def test_failed_probe_retries_without_a_session_then_startup_recovers(runtime, tenant, initializer, ping):
    await initializer._ensure(tenant.workspace_id, "local")
    del initializer.components["local"]
    checked_out = []

    async def unavailable(*args, **kwargs):
        checked_out.append(runtime.storage.engine.pool.checkedout())
        return ProbeResult("failed", "provider_unavailable")

    ping.side_effect = unavailable
    await initializer(tenant.workspace_id)
    await asyncio.gather(*runtime.tasks.pending)
    assert ping.await_count == 3
    assert checked_out == [0, 0, 0]
    assert [row.component for row in await receipts(runtime, tenant.workspace_id)] == ["local"]
    ping.side_effect = None
    await initializer.existing()
    assert len(await receipts(runtime, tenant.workspace_id)) == 2


async def test_failed_write_rolls_back_provider_and_template(runtime, tenant, initializer, monkeypatch):
    from a13n_service.provisioning import service

    original = service.insert_template

    async def failed(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("failed after template flush")

    monkeypatch.setattr(service, "insert_template", failed)
    await initializer(tenant.workspace_id)
    await asyncio.gather(*runtime.tasks.pending)
    assert await receipts(runtime, tenant.workspace_id) == []
    async with short_session(runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(EnvironmentProviderRow)) == 0
        assert await session.scalar(select(func.count()).select_from(EnvironmentTemplateRow)) == 0
    monkeypatch.setattr(service, "insert_template", original)
    await initializer.existing()
    assert len(await receipts(runtime, tenant.workspace_id)) == 2


async def test_disabled_components_and_archived_workspaces_are_skipped(runtime, tenant, initializer, ping):
    disabled = Initializer(
        runtime.storage, runtime.registry, runtime.keys, runtime.tasks, Provisioning(), runtime.endpoint_policy
    )
    await disabled(tenant.workspace_id)
    await disabled.existing()
    async with transaction(runtime.storage) as session:
        await session.execute(
            update(WorkspaceRow).where(WorkspaceRow.id == tenant.workspace_id).values(archived_at=func.now())
        )
    await initializer(tenant.workspace_id)
    await initializer.existing()
    ping.assert_not_called()
    assert await receipts(runtime, tenant.workspace_id) == []


@pytest.mark.parametrize("docker_available", [True, False])
async def test_bootstrap_and_workspace_http_creation_initialize_before_response(settings, ping, docker_available):
    if not docker_available:
        ping.return_value = ProbeResult("failed", "provider_unavailable")
    expected = 2 if docker_available else 1
    app = build_app(settings=settings)
    async with app.router.lifespan_context(app):
        runtime = app.state.runtime
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://service.test"
        ) as client:
            account = {"email": "admin@example.com", "password": "test-password-1234"}
            response = await client.post("/api/v1/auth/bootstrap", json=account)
            assert response.status_code == 200, response.text
            response = await client.post("/api/v1/auth/login", json=account)
            assert response.status_code == 200, response.text
            client.headers["x-csrf-token"] = response.json()["csrf_token"]
            workspace = (await client.get("/api/v1/workspaces")).json()["items"][0]
            assert len(await receipts(runtime, workspace["id"])) == expected
            response = await client.post(
                f"/api/v1/organizations/{workspace['organization_id']}/workspaces", json={"name": "Second"}
            )
            assert response.status_code == 201, response.text
            assert len(await receipts(runtime, response.json()["id"])) == expected


@pytest.mark.parametrize("role", ["all", "control", "worker"])
async def test_startup_catches_up_only_on_single_host_role(settings, runtime, tenant, ping, role):
    app = build_app(settings=settings, role=role)
    async with app.router.lifespan_context(app):
        assert len(await receipts(runtime, tenant.workspace_id)) == (2 if role == "all" else 0)
        assert (app.state.runtime.workspace_created is not None) == (role == "all")
    if role != "all":
        ping.assert_not_called()


async def test_manual_resources_with_the_same_names_are_not_adopted(service, initializer):
    client = service.client
    response = await client.post(
        "/api/v1/environment-providers", json={"type": "docker", "name": "Docker", "config": {}}
    )
    assert response.status_code == 201, response.text
    provider = response.json()
    response = await client.post(
        "/api/v1/environment-templates", json={"provider_id": provider["id"], "name": "Linux Sandbox"}
    )
    assert response.status_code == 201, response.text
    manual_template = response.json()
    await initializer(service.tenant.workspace_id)
    rows = await receipts(service.runtime, service.tenant.workspace_id)
    assert all(row.provider_id != provider["id"] and row.template_id != manual_template["id"] for row in rows)
    assert len((await client.get("/api/v1/environment-providers")).json()["items"]) == 3


async def test_component_environment_overrides_decode_json(clean_environment, monkeypatch):
    monkeypatch.setenv("A13N_PROVISIONING__LOCAL", '{"enabled":true,"root":"/srv/environments"}')
    monkeypatch.setenv(
        "A13N_PROVISIONING__DOCKER", '{"enabled":true,"image":"registry.example/agent:1","pull_policy":"if_missing"}'
    )
    settings = load_settings()
    assert settings.provisioning.local.enabled
    assert str(settings.provisioning.local.root) == "/srv/environments"
    assert settings.provisioning.docker.image == "registry.example/agent:1"
    assert settings.provisioning.docker.pull_policy == "if_missing"
