"""Deployment defaults require explicit workspace authority and snapshot credentials on confirmation."""

import json
from dataclasses import replace

import pytest
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.metrics_data_sources import service as sources
from a13n_service.resources.metrics_data_sources.schemas import DataSourceCreate
from a13n_service.resources.metrics_data_sources.tables import DataSourceRow
from a13n_service.settings import Monitoring, load_settings
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from a13n_service.tenancy.tables import GrantRow, PrincipalRow
from sqlalchemy import func, select

pytestmark = pytest.mark.anyio
TOKEN = "deployment-query-test-token"


def configure(service, monkeypatch, **changes):
    monitoring = Monitoring.model_validate(
        {
            "collectors": {"vmagent": {"directory": "/tmp/unused-collector", "endpoint": "http://127.0.0.1:8429"}},
            "deployment_preset": {
                "query_endpoint": "http://127.0.0.1:9090/gateway/",
                "workspace_ids": [service.tenant.workspace_id],
            },
            "deployment_query_bearer_token": TOKEN,
            **changes,
        }
    )
    runtime = replace(service.runtime, settings=service.runtime.settings.model_copy(update={"monitoring": monitoring}))
    monkeypatch.setattr(service, "runtime", runtime)
    monkeypatch.setattr(service.app.state, "runtime", runtime)


async def test_preset_confirmation_is_secret_free_and_snapshots_credentials(service, monkeypatch):
    configure(service, monkeypatch)
    base = f"{service.api}/metrics-data-sources"
    result = await service.client.get(f"{base}/deployment-preset")
    assert result.status_code == 200, result.text
    preset = result.json()
    assert TOKEN not in result.text and "bearer" not in result.text
    assert preset["has_query_credentials"]
    assert preset["config"]["query"]["endpoint"] == "http://127.0.0.1:9090/gateway"
    async with short_session(service.runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(DataSourceRow)) == 0
    body = {"name": preset["name"], "config": preset["config"], "deployment_preset_revision": preset["revision"]}
    uncredentialed = await service.client.post(base, json=body)
    assert uncredentialed.status_code == 201, uncredentialed.text
    assert not uncredentialed.json()["has_query_credentials"]
    created = await service.client.post(base, json={**body, "use_deployment_query_credentials": True})
    assert created.status_code == 201, created.text
    assert created.json()["has_query_credentials"] and TOKEN not in created.text
    source_id = created.json()["id"]
    configure(service, monkeypatch, deployment_query_bearer_token="rotated-test-token")
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(DataSourceRow, source_id)
        assert TOKEN not in json.dumps(row.query_credentials)
        assert sources.headers(service.runtime.keys, row, "query") == {"authorization": f"Bearer {TOKEN}"}
    configure(service, monkeypatch, deployment_preset=None)
    saved = await service.client.get(f"{base}/{source_id}")
    assert saved.json()["config"] == preset["config"] and saved.json()["has_query_credentials"]


@pytest.mark.parametrize("change", ["endpoint", "removed_query", "stale", "allowlist", "disabled", "collector"])
async def test_preset_rejects_changed_destination_revision_or_eligibility(service, monkeypatch, change):
    configure(service, monkeypatch)
    base = f"{service.api}/metrics-data-sources"
    preset = (await service.client.get(f"{base}/deployment-preset")).json()
    body = {
        "name": "Confirmed",
        "config": preset["config"],
        "deployment_preset_revision": preset["revision"],
        "use_deployment_query_credentials": True,
    }
    if change == "endpoint":
        body["config"]["query"]["endpoint"] = "http://127.0.0.1:9091/stolen"
    elif change == "removed_query":
        body["config"]["query"] = None
    elif change == "stale":
        body["deployment_preset_revision"] = "0" * 64
    elif change == "allowlist":
        configure(
            service,
            monkeypatch,
            deployment_preset={"query_endpoint": "http://127.0.0.1:9090/gateway", "workspace_ids": []},
        )
    elif change == "disabled":
        configure(service, monkeypatch, enabled=False)
    else:
        configure(service, monkeypatch, collectors={})
    rejected = await service.client.post(base, json=body)
    assert rejected.status_code == 400, rejected.text
    if change in {"allowlist", "disabled", "collector"}:
        assert (await service.client.get(f"{base}/deployment-preset")).json() is None
    async with short_session(service.runtime.storage) as session:
        assert await session.scalar(select(func.count()).select_from(DataSourceRow)) == 0


async def test_preset_requires_admin_even_for_discovery(service, monkeypatch):
    configure(service, monkeypatch)
    tenant, runtime = service.tenant, service.runtime
    principal_id = new_object_id("usr")
    async with transaction(runtime.storage) as session:
        session.add(PrincipalRow(id=principal_id, kind="user", name="reader", email="preset-reader@example.com"))
        await session.flush()
        session.add(
            GrantRow(
                id=new_object_id("rb"),
                organization_id=tenant.organization_id,
                workspace_id=tenant.workspace_id,
                principal_id=principal_id,
                role="builder",
                created_by_id=tenant.principal_id,
            )
        )
    builder = Principal(
        principal_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["builder"]),)
    )
    with pytest.raises(ServiceError) as error:
        await sources.deployment_preset(runtime, builder, tenant.workspace_id)
    assert error.value.code == "forbidden"
    with pytest.raises(ServiceError) as error:
        await sources.create_source(
            runtime,
            builder,
            tenant.workspace_id,
            DataSourceCreate.model_validate(
                {
                    "name": "Forged",
                    "config": {"collection": "pull", "collector": "vmagent"},
                    "deployment_preset_revision": "0" * 64,
                    "use_deployment_query_credentials": True,
                }
            ),
        )
    assert error.value.code == "forbidden"


def test_preset_environment_settings_keep_secret_separate(clean_environment, monkeypatch):
    monkeypatch.setenv(
        "A13N_MONITORING__DEPLOYMENT_PRESET",
        json.dumps({"query_endpoint": "https://metrics.example.com", "workspace_ids": ["ws_allowed"]}),
    )
    monkeypatch.setenv("A13N_MONITORING__DEPLOYMENT_QUERY_BEARER_TOKEN", TOKEN)
    settings = load_settings()
    assert settings.monitoring.deployment_preset.workspace_ids == ["ws_allowed"]
    assert TOKEN not in repr(settings.monitoring)
    assert settings.monitoring.deployment_query_bearer_token.get_secret_value() == TOKEN


@pytest.mark.parametrize("token", ["", "Bearer token", "token\n", "token\x00"])
def test_preset_invalid_tokens_fail_at_startup(token):
    with pytest.raises(ValueError):
        Monitoring(deployment_query_bearer_token=token)
