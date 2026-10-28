"""Environment connection deletion preserves references, credentials and workspace boundaries."""

from types import SimpleNamespace

import pytest
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.environment_templates.tables import EnvironmentTemplateRow
from a13n_service.runs.environments import provider_connections as environment_connections
from a13n_service.runs.environments.tables import EnvironmentRow
from sqlalchemy import select, update

from .environments_support import reserve, with_fake_providers
from .test_providers import add_workspace, create, etag, principal

pytestmark = pytest.mark.anyio


async def connection(service: SimpleNamespace) -> dict:
    return await create(
        service,
        "environment",
        {
            "type": "e2b",
            "name": "Disposable account",
            "credential": {"api_key": "never-return-this-secret"},
        },
    )


async def test_delete_requires_version_and_audits_unused_connection(service: SimpleNamespace) -> None:
    provider = await connection(service)
    path = f"{service.api}/environment-providers/{provider['id']}"
    refs = await service.client.get(path + "/references")
    assert refs.status_code == 200
    assert refs.json() == {"template_count": 0, "templates": [], "environment_count": 0, "environments": []}
    assert (await service.client.delete(path)).status_code == 428
    assert (await service.client.delete(path, headers={"if-match": '"stale:0"'})).status_code == 412
    deleted = await service.client.delete(path, headers={"if-match": etag(provider)})
    assert deleted.status_code == 204, deleted.text
    assert (await service.client.get(path)).status_code == 404
    async with short_session(service.runtime.storage) as session:
        events = (await session.scalars(select(AuditEventRow).where(AuditEventRow.target_id == provider["id"]))).all()
    assert any(event.action == "environment_provider.delete" for event in events)


async def test_reference_preview_is_bounded_and_delete_rechecks_after_preview(service: SimpleNamespace) -> None:
    provider = await connection(service)
    path = f"{service.api}/environment-providers/{provider['id']}"
    assert (await service.client.get(path + "/references")).json()["template_count"] == 0
    for index in range(12):
        response = await service.client.post(
            f"{service.api}/environment-templates",
            json={
                "name": f"Template {index}",
                "provider_id": provider["id"],
            },
        )
        assert response.status_code == 201, response.text
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(EnvironmentTemplateRow)
            .where(EnvironmentTemplateRow.provider_id == provider["id"])
            .values(enabled=False)
        )
    refs = (await service.client.get(path + "/references")).json()
    assert refs["template_count"] == 12 and len(refs["templates"]) == 10
    assert all(item["status"] == "disabled" for item in refs["templates"])
    blocked = await service.client.delete(path, headers={"if-match": etag(provider)})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["details"]["reason"] == "provider_in_use"
    assert blocked.json()["error"]["details"]["references"] == refs
    assert "never-return-this-secret" not in blocked.text
    assert (await service.client.get(path)).status_code == 200


async def test_historical_environment_alone_blocks_deletion(service: SimpleNamespace) -> None:
    env = await with_fake_providers(service)
    instance = await reserve(env, env.template["id"], name="Historical desktop")
    # Keep the historical instance while the template now uses another account.
    other = await create(service, "environment", {"type": "fake", "name": "Other"})
    async with transaction(service.runtime.storage) as session:
        await session.execute(
            update(EnvironmentTemplateRow)
            .where(EnvironmentTemplateRow.id == env.template["id"])
            .values(provider_id=other["id"])
        )
        await session.execute(
            update(EnvironmentRow)
            .where(EnvironmentRow.id == instance["id"])
            .values(status="deleted", operation_id=None, operation_started_at=None, operation_deadline=None)
        )
    path = f"{service.api}/environment-providers/{env.provider['id']}"
    refs = (await service.client.get(path + "/references")).json()
    assert refs["template_count"] == 0
    assert refs["environments"] == [{"id": instance["id"], "name": "Historical desktop", "status": "deleted"}]
    blocked = await service.client.delete(path, headers={"if-match": etag(env.provider)})
    assert blocked.status_code == 409, blocked.text


async def test_references_and_deletion_respect_workspace_and_write_access(service: SimpleNamespace) -> None:
    provider = await connection(service)
    workspace = service.tenant.workspace_id
    viewer = principal(service, (workspace, "viewer"))
    refs = await environment_connections.references(service.runtime.storage, viewer, workspace, provider["id"])
    assert refs.template_count == 0
    with pytest.raises(ServiceError) as denied:
        await environment_connections.delete(
            service.runtime.storage, viewer, workspace, provider["id"], if_match=etag(provider)
        )
    assert denied.value.code == "forbidden"
    other_workspace = await add_workspace(service)
    actor = principal(service, (other_workspace, "admin"))
    for operation in (environment_connections.references, environment_connections.delete):
        kwargs = {"if_match": etag(provider)} if operation is environment_connections.delete else {}
        with pytest.raises(ServiceError) as missing:
            await operation(service.runtime.storage, actor, other_workspace, provider["id"], **kwargs)
        assert missing.value.code == "not_found"


async def test_connection_reference_query_and_delete_are_available_through_mcp(service: SimpleNamespace) -> None:
    from a13n_service.api_tools import tool_name

    from .mcp_support import call, client, key, rpc

    provider = await connection(service)
    async with client(service, await key(service)) as http:
        listing = (await rpc(http, "tools/list")).json()["result"]["tools"]
        path = "/environment-providers/{provider_id}/references"
        operation = service.app.openapi()["paths"]["/api/v1" + path]["get"]
        assert tool_name(operation["operationId"]) in {tool["name"] for tool in listing}
        result = await call(http, service, "GET", path, provider_id=provider["id"])
        assert result["status"] == 200 and result["body"]["environment_count"] == 0
        result = await call(
            http,
            service,
            "DELETE",
            "/environment-providers/{provider_id}",
            provider_id=provider["id"],
            expected_version=etag(provider),
        )
        assert result["status"] == 204
