"""Durable editing, scope, concurrency and query-authority separation."""

import asyncio

import pytest
from a13n_service.infra.db import transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.providers.metrics import InstantQuery
from a13n_service.resources.dashboards import service as dashboards
from a13n_service.resources.dashboards.schemas import (
    DashboardConfig,
    DashboardCreate,
    DashboardUpdate,
    PromQLQuery,
    default_config,
)
from a13n_service.resources.metrics_data_sources import service as sources
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from a13n_service.tenancy.tables import PrincipalRow
from pydantic import ValidationError

pytestmark = pytest.mark.anyio


async def test_default_is_unique_editable_and_reset_uses_the_same_document(service):
    base = f"{service.api}/metrics-dashboards"
    results = await asyncio.gather(*(service.client.get(base) for _ in range(4)))
    assert all(result.status_code == 200 for result in results)
    documents = [result.json()["items"] for result in results]
    assert all(items == documents[0] for items in documents)
    document = documents[0][0]
    assert document["builtin"]
    item = f"{base}/{document['id']}"
    original = await service.client.get(item)
    config = document["config"]
    config["panels"] = config["panels"][:1]
    match = {"if-match": original.headers["etag"]}
    assert (await service.client.patch(item, json={"config": config})).status_code == 428
    saved = await service.client.patch(item, json={"config": config}, headers=match)
    assert saved.status_code == 200, saved.text
    assert saved.json()["config"] == config
    assert (await service.client.get(item)).json() == saved.json()
    assert (await service.client.patch(item, json={"name": "stale"}, headers=match)).status_code == 412
    match = {"if-match": saved.headers["etag"]}
    noop = await service.client.patch(item, json={"config": config}, headers=match)
    assert noop.headers["etag"] == saved.headers["etag"]
    assert (await service.client.delete(item, headers=match)).status_code == 409
    preset = await service.client.get(f"{base}/preset")
    reset = await service.client.patch(item, json={"config": preset.json()}, headers=match)
    assert reset.status_code == 200
    assert len(reset.json()["config"]["panels"]) == 10


async def test_custom_dashboard_create_update_race_and_delete(service):
    base = f"{service.api}/metrics-dashboards"
    created = await service.client.post(base, json={"name": "Custom", "config": {"panels": []}})
    assert created.status_code == 201, created.text
    item = f"{base}/{created.json()['id']}"
    assert not created.json()["builtin"]
    headers = {"if-match": created.headers["etag"]}
    changes = await asyncio.gather(
        *(service.client.patch(item, json={"name": f"Change {index}"}, headers=headers) for index in range(2))
    )
    assert sorted(result.status_code for result in changes) == [200, 412]
    current = await service.client.get(item)
    assert (await service.client.delete(item, headers=headers)).status_code == 412
    assert (await service.client.delete(item, headers={"if-match": current.headers["etag"]})).status_code == 204
    assert (await service.client.get(item)).status_code == 404


async def test_dashboard_editing_does_not_grant_arbitrary_query_authority(service):
    tenant, storage = service.tenant, service.runtime.storage
    async with transaction(storage) as session:
        actor_id = new_object_id("usr")
        session.add(PrincipalRow(id=actor_id, kind="user", name="Builder", email="dashboard-builder@example.com"))
    actor = Principal(
        actor_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["builder"]),)
    )
    config = default_config()
    config.panels[0].queries = [
        # Stored as inert text, never evaluated by document CRUD.
        PromQLQuery(source_id="mds_missing", expression="up")
    ]
    body = DashboardCreate.model_validate({"name": "Queries", "config": config.model_dump()})
    created = await dashboards.create_dashboard(storage, actor, tenant.workspace_id, body)
    assert created.config.panels[0].queries[0].kind == "promql"
    with pytest.raises(ServiceError) as denied:
        await sources.query_source(
            service.runtime, actor, tenant.workspace_id, "mds_missing", InstantQuery(expression="up")
        )
    assert denied.value.code == "forbidden"
    viewer = Principal(
        actor_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["viewer"]),)
    )
    assert (await dashboards.get_dashboard(storage, viewer, tenant.workspace_id, created.id)).id == created.id
    with pytest.raises(ServiceError) as denied:
        await dashboards.update_dashboard(
            storage, viewer, tenant.workspace_id, created.id, DashboardUpdate(name="No"), if_match=f'"{created.id}:1"'
        )
    assert denied.value.code == "forbidden"
    outsider = Principal(actor_id, "user", ())
    with pytest.raises(ServiceError):
        await dashboards.get_dashboard(storage, outsider, tenant.workspace_id, created.id)


def test_panel_schema_bounds_and_default_roundtrip():
    preset = default_config()
    assert DashboardConfig.model_validate_json(preset.model_dump_json()) == preset
    panel = preset.panels[0].model_dump()
    for config in (
        {"panels": [panel, panel]},
        {"panels": [dict(panel, position={"x": 10, "y": 0, "w": 6, "h": 5})]},
        {"schema_version": 2},
        {"refresh_seconds": 1},
        {"panels": [dict(panel, queries=[{"kind": "metric", "metric": "unbounded"}])]},
    ):
        with pytest.raises(ValidationError):
            DashboardConfig.model_validate(config)
