"""Source credentials, conditional changes, scope and Environment selection against PostgreSQL."""

import json

import pytest
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.resources.metrics_data_sources import service as sources
from a13n_service.resources.metrics_data_sources.schemas import DataSourceCreate
from a13n_service.resources.metrics_data_sources.tables import DataSourceRow
from a13n_service.runs.environments.tables import EnvironmentRow
from a13n_service.tenancy.authorize import BUILT_IN_ROLES, Grant, Principal
from a13n_service.tenancy.tables import GrantRow, PrincipalRow
from sqlalchemy import select

pytestmark = pytest.mark.anyio


def body() -> dict:
    return {
        "name": "Native",
        "config": {
            "collection": "native",
            "export": {"endpoint": "http://127.0.0.1:4318/v1/metrics"},
            "query": {"endpoint": "http://127.0.0.1:9090/gateway", "scope_labels": {"tenant": "ours"}},
        },
        "export_credentials": {"headers": {"Authorization": "Bearer ingestion-secret"}},
        "query_credentials": {"headers": {"Authorization": "Bearer query-secret"}},
    }


async def test_sources_preserve_write_only_credentials_and_conditional_updates(service) -> None:
    base = f"{service.api}/metrics-data-sources"
    created = await service.client.post(base, json=body())
    assert created.status_code == 201, created.text
    source = created.json()
    assert source["has_export_credentials"] and source["has_query_credentials"]
    assert "secret" not in created.text
    item = f"{base}/{source['id']}"
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(DataSourceRow, source["id"])
        assert "secret" not in json.dumps([row.export_credentials, row.query_credentials])
        assert sources.headers(service.runtime.keys, row, "export") == {"authorization": "Bearer ingestion-secret"}
        assert sources.headers(service.runtime.keys, row, "query") == {"authorization": "Bearer query-secret"}

    assert (await service.client.patch(item, json={"name": "renamed"})).status_code == 428
    changed = await service.client.patch(item, json={"name": "renamed"}, headers={"if-match": created.headers["etag"]})
    assert changed.status_code == 200, changed.text
    assert changed.json()["has_query_credentials"]
    stale = await service.client.patch(item, json={"enabled": False}, headers={"if-match": created.headers["etag"]})
    assert stale.status_code == 412
    unchanged = await service.client.patch(
        item, json={"name": "renamed"}, headers={"if-match": changed.headers["etag"]}
    )
    assert unchanged.headers["etag"] == changed.headers["etag"]

    config = source["config"]
    config["query"]["endpoint"] = "http://127.0.0.1:9091/new"
    rejected = await service.client.patch(item, json={"config": config}, headers={"if-match": changed.headers["etag"]})
    assert rejected.status_code == 400
    cleared = await service.client.patch(
        item, json={"config": config, "query_credentials": None}, headers={"if-match": changed.headers["etag"]}
    )
    assert cleared.status_code == 200, cleared.text
    assert not cleared.json()["has_query_credentials"] and cleared.json()["has_export_credentials"]
    async with short_session(service.runtime.storage) as session:
        events = (await session.scalars(select(AuditEventRow).where(AuditEventRow.target_id == source["id"]))).all()
        assert len(events) == 3
        assert "secret" not in json.dumps([event.details for event in events])
    assert (await service.client.delete(item, headers={"if-match": cleared.headers["etag"]})).status_code == 204


async def test_local_cache_is_explicit_unique_and_fixed(service) -> None:
    base = f"{service.api}/metrics-data-sources"
    first = await service.client.get(base)
    second = await service.client.get(base)
    assert first.status_code == 200, first.text
    assert first.json() == second.json()
    source = first.json()["items"][0]
    assert source["builtin"] and source["config"]["collection"] == "proxy"
    assert source["config"]["export"] is None and source["config"]["query"] is None
    item = f"{base}/{source['id']}"
    match = {"if-match": f'"{source["id"]}:{source["version"]}"'}
    assert (await service.client.patch(item, json={"enabled": False}, headers=match)).status_code == 409
    assert (await service.client.delete(item, headers=match)).status_code == 409


async def test_binding_is_conditional_and_prevents_source_deletion(service) -> None:
    base = f"{service.api}/metrics-data-sources"
    source = await service.client.post(base, json=body())
    source_id = source.json()["id"]
    tenant = service.tenant
    environment_id = new_object_id("env")
    async with transaction(service.runtime.storage) as session:
        session.add(
            EnvironmentRow(
                id=environment_id,
                organization_id=tenant.organization_id,
                workspace_id=tenant.workspace_id,
                name="External",
                status="ready",
                device_id="a-device",
                endpoint="http://127.0.0.1:9",
                token={"test": "encrypted-placeholder"},
                owner_principal_id=tenant.principal_id,
                created_by_id=tenant.principal_id,
            )
        )
    item = f"{service.api}/environments/{environment_id}"
    environment = await service.client.get(item)
    assert environment.json()["metrics_data_source_id"] is None
    binding = {"metrics_data_source_id": source_id}
    assert (await service.client.put(f"{item}/metrics-binding", json=binding)).status_code == 428
    selected = await service.client.put(
        f"{item}/metrics-binding", json=binding, headers={"if-match": environment.headers["etag"]}
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["metrics_data_source_id"] == source_id
    deletion = await service.client.delete(f"{base}/{source_id}", headers={"if-match": source.headers["etag"]})
    assert deletion.status_code == 409 and deletion.json()["error"]["details"]["reason"] == "data_source_in_use"
    rejected = await service.client.put(
        f"{item}/metrics-binding",
        json={**binding, "metrics_labels": {"tenant": "other"}},
        headers={"if-match": selected.headers["etag"]},
    )
    assert rejected.status_code == 400
    cleared = await service.client.put(
        f"{item}/metrics-binding", json={"metrics_data_source_id": None}, headers={"if-match": selected.headers["etag"]}
    )
    assert cleared.status_code == 200
    assert (
        await service.client.delete(f"{base}/{source_id}", headers={"if-match": source.headers["etag"]})
    ).status_code == 204


async def test_source_administration_rechecks_admin_authority(service) -> None:
    tenant, runtime = service.tenant, service.runtime
    principal_id = new_object_id("usr")
    async with transaction(runtime.storage) as session:
        session.add(PrincipalRow(id=principal_id, kind="user", name="builder", email="builder-metrics@example.com"))
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
        await sources.create_source(runtime, builder, tenant.workspace_id, DataSourceCreate.model_validate(body()))
    assert error.value.code == "forbidden"
    assert (
        (await sources.list_sources(runtime.storage, builder, tenant.workspace_id, limit=10, cursor=None))
        .items[0]
        .builtin
    )


@pytest.mark.parametrize("kind,limit", [("metrics-data-sources", 128), ("devices", 512)])
async def test_admin_serialization_preserves_monitoring_count_limits(service, monkeypatch, kind, limit):
    import asyncio

    from a13n_service.runs.devices.tables import DeviceRow
    from a13n_service.tenancy import access
    from sqlalchemy import func

    tenant = service.tenant
    row_type = DataSourceRow if kind == "metrics-data-sources" else DeviceRow
    async with transaction(service.runtime.storage) as session:
        for index in range(limit - 1):
            fields = dict(
                organization_id=tenant.organization_id,
                workspace_id=tenant.workspace_id,
                name=f"Existing {index}",
                created_by_id=tenant.principal_id,
                updated_by_id=tenant.principal_id,
            )
            if row_type is DataSourceRow:
                session.add(DataSourceRow(id=new_object_id("mds"), config={"collection": "proxy"}, **fields))
            else:
                session.add(
                    DeviceRow(id=new_object_id("dev"), device_id=f"existing-{index}", token_hash="0" * 64, **fields)
                )
    original_lock = access.lock_organization
    arrived = 0
    together = asyncio.Event()

    async def lock(session, organization_id):
        nonlocal arrived
        arrived += 1
        if arrived == 4:
            together.set()
        await asyncio.wait_for(together.wait(), 5)
        await original_lock(session, organization_id)

    monkeypatch.setattr(access, "lock_organization", lock)
    responses = await asyncio.gather(
        *[
            service.client.post(
                f"{service.api}/{kind}",
                json={
                    "name": f"Concurrent {index}",
                    **(
                        {"config": {"collection": "proxy"}}
                        if row_type is DataSourceRow
                        else {"device_id": f"new-{index}"}
                    ),
                },
            )
            for index in range(4)
        ]
    )
    assert sorted(response.status_code for response in responses) == [201, 409, 409, 409]
    async with short_session(service.runtime.storage) as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(row_type).where(row_type.workspace_id == tenant.workspace_id)
            )
            == limit
        )


@pytest.mark.parametrize("channel", ["export", "query", "collection"])
async def test_removing_source_capability_clears_only_its_credentials(service, channel):
    base = f"{service.api}/metrics-data-sources"
    payload = body()
    if channel == "export":
        payload["config"]["collection"] = "proxy"
    created = await service.client.post(base, json=payload)
    assert created.status_code == 201, created.text
    source = created.json()
    config = source["config"]
    removed = "export" if channel == "collection" else channel
    config[removed] = None
    if channel == "collection":
        config["collection"] = "external"
    changed = await service.client.patch(
        f"{base}/{source['id']}",
        json={"config": config},
        headers={"if-match": created.headers["etag"]},
    )
    assert changed.status_code == 200, changed.text
    retained = "query" if removed == "export" else "export"
    assert not changed.json()[f"has_{removed}_credentials"]
    assert changed.json()[f"has_{retained}_credentials"]
    async with short_session(service.runtime.storage) as session:
        row = await session.get_one(DataSourceRow, source["id"])
        assert sources.headers(service.runtime.keys, row, removed) == {}
        assert sources.headers(service.runtime.keys, row, retained) == {
            key.lower(): value for key, value in payload[f"{retained}_credentials"]["headers"].items()
        }


@pytest.mark.parametrize("port", ["abc", "65536", "-1"])
async def test_source_endpoint_rejects_invalid_ports_before_network_access(service, port):
    payload = body()
    payload["config"]["export"]["endpoint"] = f"http://localhost:{port}/v1/metrics"
    response = await service.client.post(f"{service.api}/metrics-data-sources", json=payload)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_argument"


async def test_writer_cannot_erase_admin_mapping_by_replacing_the_same_binding(service):
    from a13n_service.runs.environments.monitoring.bindings import select_source

    tenant = service.tenant
    source = (await service.client.get(f"{service.api}/metrics-data-sources")).json()["items"][0]
    writer = Principal(
        tenant.principal_id, "user", (Grant(tenant.organization_id, tenant.workspace_id, BUILT_IN_ROLES["builder"]),)
    )
    environment = EnvironmentRow(
        id=new_object_id("env"),
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        name="Mapped target",
        status="ready",
        metrics_data_source_id=source["id"],
        metrics_labels={"cluster": "trusted"},
        created_by_id=tenant.principal_id,
    )
    async with short_session(service.runtime.storage) as session:
        with pytest.raises(ServiceError) as error:
            await select_source(session, writer, environment, source["id"], {})
        assert error.value.code == "forbidden"
        assert environment.metrics_labels == {"cluster": "trusted"}
        assert not await select_source(session, writer, environment, source["id"], {"cluster": "trusted"})
        assert await select_source(session, writer, environment, None, {})
        assert environment.metrics_data_source_id is None and environment.metrics_labels == {}
