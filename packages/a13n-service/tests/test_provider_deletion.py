"""Unused providers can be removed; retained references, permissions and versions protect them."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from a13n_service.infra.audit import AuditEventRow
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.models.tables import ModelRow
from a13n_service.resources.providers.tables import MemoryProviderRow, ModelProviderOAuthRow, ModelProviderRow
from a13n_service.runs.provider_deletion import delete_provider
from sqlalchemy import select

from .test_providers import add_workspace, create, etag, principal

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("kind", "body"),
    [
        ("model", {"type": "openai", "name": "Unused", "credential": {"api_key": "secret"}}),
        ("web", {"type": "duckduckgo", "name": "Unused"}),
        ("connector", {"type": "composio", "name": "Unused", "credential": {"api_key": "secret"}}),
        ("memory", {"type": "mem0", "name": "Unused", "credential": {"api_key": "secret"}}),
    ],
)
async def test_delete_unused_provider(service: SimpleNamespace, kind: str, body: dict) -> None:
    if kind == "memory":
        async with transaction(service.runtime.storage) as session:
            row = MemoryProviderRow(
                id="memprov_unused",
                organization_id=service.tenant.organization_id,
                workspace_id=service.tenant.workspace_id,
                type="external",
                name="Unused",
                config={},
                credential=None,
            )
            session.add(row)
            await session.flush()
            provider = {"id": row.id, "version": row.version}
    else:
        provider = await create(service, kind, body)
    path = f"{service.api}/{kind}-providers/{provider['id']}"
    assert (await service.client.delete(path)).status_code == 428
    assert (await service.client.delete(path, headers={"if-match": '"stale:1"'})).status_code == 412
    response = await service.client.delete(path, headers={"if-match": etag(provider)})
    assert response.status_code == 204, response.text
    assert (await service.client.get(path)).status_code == 404
    async with short_session(service.runtime.storage) as session:
        assert await session.scalar(
            select(AuditEventRow.id).where(
                AuditEventRow.target_id == provider["id"],
                AuditEventRow.action == f"{kind}_provider.delete",
            )
        )


async def test_model_reference_and_workspace_permissions(service: SimpleNamespace) -> None:
    provider = await create(service, "model", {"type": "openai", "name": "Used", "credential": {"api_key": "secret"}})
    viewer = principal(service, (service.tenant.workspace_id, "viewer"))
    with pytest.raises(ServiceError) as denied:
        await delete_provider(
            service.runtime.storage,
            viewer,
            ModelProviderRow,
            service.tenant.workspace_id,
            provider["id"],
            if_match=etag(provider),
        )
    assert denied.value.code == "forbidden"
    other = await add_workspace(service)
    response = await service.client.delete(
        f"{service.api}/model-providers/{provider['id']}", headers={"if-match": etag(provider), "x-workspace-id": other}
    )
    assert response.status_code == 404
    async with transaction(service.runtime.storage) as session:
        session.add(
            ModelRow(
                id="mdl_delete_test",
                organization_id=service.tenant.organization_id,
                workspace_id=service.tenant.workspace_id,
                provider_id=provider["id"],
                key="delete-test",
                name="Disabled model",
                config={},
                enabled=False,
                created_by_id=service.tenant.principal_id,
                updated_by_id=service.tenant.principal_id,
            )
        )
    response = await service.client.delete(
        f"{service.api}/model-providers/{provider['id']}", headers={"if-match": etag(provider)}
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "provider_in_use"


async def test_model_oauth_state_removed_with_provider(service: SimpleNamespace) -> None:
    provider = await create(
        service, "model", {"type": "openai", "name": "OAuth fixture", "credential": {"api_key": "secret"}}
    )
    async with transaction(service.runtime.storage) as session:
        session.add(
            ModelProviderOAuthRow(
                provider_id=provider["id"],
                organization_id=service.tenant.organization_id,
                workspace_id=service.tenant.workspace_id,
                host_id="host_test",
            )
        )
    response = await service.client.delete(
        f"{service.api}/model-providers/{provider['id']}", headers={"if-match": etag(provider)}
    )
    assert response.status_code == 204, response.text
    async with short_session(service.runtime.storage) as session:
        assert await session.get(ModelProviderOAuthRow, provider["id"]) is None


async def test_web_provider_retained_revision_blocks_delete(service: SimpleNamespace) -> None:
    from .test_agents import create_agent, create_model

    provider = await create(service, "web", {"type": "duckduckgo", "name": "Search"})
    model = await create_model(service)
    agent = await create_agent(
        service,
        "Search agent",
        model,
        toolsets={
            "web": {"tools": {"search": {"config": {"provider_id": provider["id"]}}}},
        },
    )
    # A new default without search does not erase the old revision's reference.
    response = await service.client.post(
        f"{service.api}/agents/{agent['id']}/revisions",
        json={"config": {"model": model}},
        headers={"if-match": etag(agent)},
    )
    assert response.status_code == 201, response.text
    response = await service.client.delete(
        f"{service.api}/web-providers/{provider['id']}", headers={"if-match": etag(provider)}
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "provider_in_use"


async def test_pending_memory_purge_retains_provider(service: SimpleNamespace) -> None:
    from a13n_service.infra.outbox import OutboxRow

    async with transaction(service.runtime.storage) as session:
        row = MemoryProviderRow(
            id="memprov_cleanup",
            organization_id=service.tenant.organization_id,
            workspace_id=service.tenant.workspace_id,
            type="external",
            name="Cleanup",
            config={},
            credential=None,
        )
        session.add(row)
        session.add(
            OutboxRow(
                id="ob_cleanup",
                organization_id=service.tenant.organization_id,
                workspace_id=service.tenant.workspace_id,
                kind="memory_purge",
                dedupe_key="cleanup-test",
                # Keep the purge pending while the live control loop runs.
                available_at=datetime.now(UTC) + timedelta(days=1),
                target={"provider_id": row.id, "namespace": "old"},
                payload={},
            )
        )
        await session.flush()
        version = row.version
    response = await service.client.delete(
        f"{service.api}/memory-providers/{row.id}", headers={"if-match": etag({"id": row.id, "version": version})}
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"] == "provider_in_use"


async def test_web_reference_read_holds_key_share_until_commit(service: SimpleNamespace) -> None:
    from a13n_service.resources.providers.service import resolve_provider
    from a13n_service.resources.providers.tables import WebProviderRow
    from a13n_service.tenancy.authorize import WorkspaceScope
    from sqlalchemy.exc import OperationalError

    provider = await create(service, "web", {"type": "duckduckgo", "name": "Search"})
    actor = principal(service, (service.tenant.workspace_id, "admin"))
    scope = WorkspaceScope(service.tenant.organization_id, service.tenant.workspace_id)
    async with transaction(service.runtime.storage) as selection:
        await resolve_provider(selection, actor, WebProviderRow, scope, provider["id"])
        # Deletion's row lock cannot pass a still-uncommitted reference validation.
        with pytest.raises(OperationalError) as blocked:
            async with transaction(service.runtime.storage) as deleting:
                await deleting.scalar(
                    select(WebProviderRow).where(WebProviderRow.id == provider["id"]).with_for_update(nowait=True)
                )
        assert getattr(blocked.value.orig, "sqlstate", None) == "55P03"
    response = await service.client.delete(
        f"{service.api}/web-providers/{provider['id']}", headers={"if-match": etag(provider)}
    )
    assert response.status_code == 204, response.text
