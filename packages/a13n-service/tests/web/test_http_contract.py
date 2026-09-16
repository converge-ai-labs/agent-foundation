"""Service-owned HTTP evidence for external clients, without importing an SDK."""

import httpx2
import pytest
from a13n_service.app import Components, create_app

from ..models.conftest import WORKSPACE_ID, actor
from ..models.test_router import settings

pytestmark = pytest.mark.anyio


async def test_web_provider_crud_response_metadata(web_sessions, service_database, tmp_path):
    async def authenticate(_request):
        return actor()

    app = create_app(settings(tmp_path, service_database), components=Components(request_authenticator=authenticate))
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(base_url="http://testserver", transport=httpx2.ASGITransport(app=app)) as client:
            path = f"/api/v1/workspaces/{WORKSPACE_ID}/web-providers"
            created = await client.post(
                path,
                json={"type": "brave", "name": "Client account", "credential": {"api_key": "integration-secret"}},
            )
            assert created.status_code == 201
            assert b"integration-secret" not in created.content
            etag = created.headers["ETag"]
            assert created.headers["X-Request-ID"]
            item_path = f"{path}/{created.json()['id']}"
            fetched = await client.get(item_path)
            assert fetched.status_code == 200
            assert fetched.headers["ETag"] == etag
            changed = await client.patch(item_path, headers={"If-Match": etag}, json={"enabled": False})
            assert changed.status_code == 200
            assert changed.json()["enabled"] is False
            assert changed.headers["ETag"] != etag
            conflict = await client.patch(item_path, headers={"If-Match": etag}, json={"enabled": True})
            assert conflict.status_code == 412
            assert conflict.json()["error"]["request_id"] == conflict.headers["X-Request-ID"]
