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


async def test_installed_web_manifest_schema_save_rotation_and_probe(
    web_sessions, service_database, tmp_path, monkeypatch
):
    """A genuinely installed extension traverses schema, HTTP, encryption, and dispatch."""
    import json
    from pathlib import Path

    from a13n_service.storage import short_session
    from a13n_service.web.models import WebProviderRecord
    from anyio import run_process

    repository = Path(__file__).resolve().parents[4]
    target = tmp_path / "installed"
    await run_process(
        [
            "uv",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(target),
            str(repository / "examples/provider-plugin"),
        ]
    )
    monkeypatch.syspath_prepend(str(target))

    async def authenticate(_request):
        return actor()

    configuration = settings(tmp_path, service_database)
    configuration = configuration.model_copy(
        update={"provider_plugins": configuration.provider_plugins.model_copy(update={"enabled": ("acme",)})}
    )
    from a13n_harness.providers.endpoint_policy import EndpointPolicy
    from a13n_harness.providers.web import WebProviderTransport

    observed = []

    class Policy(EndpointPolicy):
        async def validate(self, endpoint):
            assert endpoint == "https://search.acme.example/v1/search"
            self.validate_syntax(endpoint)

    def vendor(request):
        body = json.loads(request.content)
        observed.append((body["index"], request.headers["Authorization"]))
        return httpx2.Response(200, json={"results": []})

    app = create_app(configuration, components=Components(request_authenticator=authenticate))
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.MockTransport(vendor)) as vendor_client,
    ):
        app.state.runtime.control.web_providers.registry.transport = WebProviderTransport(
            client=vendor_client, endpoint_policy=Policy()
        )
        async with httpx2.AsyncClient(base_url="http://testserver", transport=httpx2.ASGITransport(app=app)) as client:
            catalog = await client.get("/api/v1/web-provider-types/acme_web")
            assert catalog.status_code == 200, catalog.text
            assert "index" in catalog.json()["configuration_schema"]["properties"]
            assert "token" in catalog.json()["credential_schema"]["properties"]
            path = f"/api/v1/workspaces/{WORKSPACE_ID}/web-providers"
            invalid = await client.post(
                path,
                json={
                    "type": "acme_web",
                    "name": "Invalid",
                    "configuration": {"index": "guides"},
                    "credential": {"api_key": "wrong-contract"},
                },
            )
            assert invalid.status_code == 400 and "wrong-contract" not in invalid.text
            created = await client.post(
                path,
                json={
                    "type": "acme_web",
                    "name": "Installed",
                    "configuration": {"index": "guides"},
                    "credential": {"token": "initial-secret"},
                },
            )
            assert created.status_code == 201, created.text
            provider_id = created.json()["id"]
            item_path = f"{path}/{provider_id}"
            assert "initial-secret" not in created.text
            async with short_session(web_sessions) as session:
                record = await session.get(WebProviderRecord, provider_id)
                assert record is not None
                assert json.loads(record.credential_snapshot().decrypt(configuration.secret_protector())) == {
                    "token": "initial-secret"
                }
            probe = await client.post(item_path + "/test", json={})
            assert probe.status_code == 200 and probe.json()["success"], probe.text
            rotated = await client.patch(
                item_path,
                headers={"If-Match": created.headers["ETag"]},
                json={
                    "configuration": {"index": "reference"},
                    "credential": {"token": "rotated-secret"},
                },
            )
            assert rotated.status_code == 200, rotated.text
            async with short_session(web_sessions) as session:
                record = await session.get(WebProviderRecord, provider_id)
                assert record is not None
                assert record.configuration == {"index": "reference"}
                assert json.loads(record.credential_snapshot().decrypt(configuration.secret_protector())) == {
                    "token": "rotated-secret"
                }
            probe = await client.post(item_path + "/test", json={})
            assert probe.status_code == 200 and probe.json()["success"], probe.text
            disabled = await client.patch(
                item_path, headers={"If-Match": rotated.headers["ETag"]}, json={"enabled": False}
            )
            assert disabled.status_code == 200
            probe = await client.post(item_path + "/test", json={})
            assert probe.status_code == 409 and probe.json()["error"]["code"] == "web_provider_disabled"

            assert observed == [("guides", "Bearer initial-secret"), ("reference", "Bearer rotated-secret")]
