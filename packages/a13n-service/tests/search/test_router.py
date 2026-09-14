import httpx2
import pytest
from a13n_service.app import Components, create_app
from a13n_service.iam.models import OrganizationRecord, WorkspaceRecord
from a13n_service.storage import transaction
from sqlalchemy import update

from ..models.conftest import NOW, ORG_ID, WORKSPACE_ID, actor
from ..models.test_router import settings
from ..resource_scope_helpers import organization_admin

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("workspace_ref, organization_ref", [(WORKSPACE_ID, ORG_ID), ("default", "test")])
async def test_http_contract_safe_credentials_preconditions_and_catalog(
    search_sessions, service_sqlite_database, tmp_path, monkeypatch, workspace_ref, organization_ref
):
    admin = await organization_admin(search_sessions, actor())

    async def authenticate(request):
        return admin if request.headers.get("test-scope") == "organization" else actor()

    app = create_app(
        settings(tmp_path, service_sqlite_database), components=Components(request_authenticator=authenticate)
    )
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as client:
            catalog = await client.get("/api/v1/search-provider-types")
            assert catalog.status_code == 200, catalog.text
            assert [item["type"] for item in catalog.json()["items"]] == ["brave", "exa"]
            for definition in catalog.json()["items"]:
                assert definition["credential_schema"]["writeOnly"]
                assert (await client.get(f"/api/v1/search-provider-types/{definition['type']}")).json() == definition
            for scope, headers in (
                (f"workspaces/{workspace_ref}", {}),
                (f"organizations/{organization_ref}", {"test-scope": "organization"}),
            ):
                path = f"/api/v1/{scope}/search-providers"
                bad = await client.post(
                    path,
                    headers=headers,
                    json={
                        "type": "brave",
                        "name": "Bad",
                        "credential": "top-secret",
                        "configuration": {"endpoint": "https://example.com"},
                    },
                )
                assert bad.status_code == 400 and "top-secret" not in bad.text
                created = await client.post(
                    path, headers=headers, json={"type": "brave", "name": "Brave", "credential": "top-secret"}
                )
                assert created.status_code == 201, created.text
                assert "top-secret" not in created.text and "credential" not in created.json()
                assert created.json()["credential_configured"]
                item_path = f"{path}/{created.json()['id']}"
                assert (await client.get(item_path, headers=headers)).headers["etag"] == created.headers["etag"]
                assert (await client.patch(item_path, headers=headers, json={"enabled": False})).status_code == 428
                assert (
                    await client.patch(item_path, headers={**headers, "If-Match": '"old"'}, json={"enabled": False})
                ).status_code == 412
                changed = await client.patch(
                    item_path, headers={**headers, "If-Match": created.headers["etag"]}, json={"enabled": False}
                )
                assert changed.status_code == 200 and not changed.json()["enabled"]
                assert changed.headers["etag"] != created.headers["etag"]
                references = await client.get(item_path + "/references", headers=headers)
                assert references.json() == {"items": [], "next_cursor": None}
                tested = await client.post(item_path + "/test", headers=headers, json={})
                assert tested.status_code == 409 and tested.json()["error"]["code"] == "search_provider_disabled"
            assert (await client.get(f"/api/v1/organizations/{ORG_ID}/search-providers")).status_code == 404


async def test_search_reference_identity_rename_and_credential_boundaries(
    search_sessions,
    service_sqlite_database,
    tmp_path,
):
    admin = await organization_admin(search_sessions, actor())

    async def authenticate(request):
        return admin if request.headers.get("test-scope") == "organization" else actor()

    async with transaction(search_sessions) as session:
        session.add(OrganizationRecord(id="org_other", key="other-org", name="Other", created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id="ws_other", organization_id=ORG_ID, key="other", name="Other", created_at=NOW, updated_at=NOW
            )
        )
    app = create_app(
        settings(tmp_path, service_sqlite_database), components=Components(request_authenticator=authenticate)
    )
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as client:
            for plural, resource_id, old_key, new_key, model, headers in (
                ("workspaces", WORKSPACE_ID, "default", "research", WorkspaceRecord, {}),
                ("organizations", ORG_ID, "test", "acme", OrganizationRecord, {"test-scope": "organization"}),
            ):
                prefix = f"/api/v1/{plural}"
                created = await client.post(
                    f"{prefix}/{old_key}/search-providers",
                    headers=headers,
                    json={"type": "brave", "name": "Research", "credential": "test-secret"},
                )
                assert created.status_code == 201, created.text
                account = created.json()
                assert account["organization_id"] == ORG_ID
                assert account["workspace_id"] == (WORKSPACE_ID if plural == "workspaces" else None)
                suffix = f"search-providers/{account['id']}"
                assert (await client.get(f"{prefix}/{resource_id}/{suffix}", headers=headers)).json() == account
                async with transaction(search_sessions) as session:
                    await session.execute(update(model).where(model.id == resource_id).values(key=new_key))
                assert (await client.get(f"{prefix}/{old_key}/{suffix}", headers=headers)).status_code == 404
                for reference in (resource_id, new_key):
                    response = await client.get(f"{prefix}/{reference}/{suffix}", headers=headers)
                    assert response.json() == account
                    assert response.headers["ETag"] == created.headers["ETag"]
            for reference in ("ws_other", "other"):
                assert (await client.get(f"/api/v1/workspaces/{reference}/search-providers")).status_code == 404
            for reference in ("org_other", "other-org"):
                response = await client.get(
                    f"/api/v1/organizations/{reference}/search-providers", headers={"test-scope": "organization"}
                )
                assert response.status_code == 404
