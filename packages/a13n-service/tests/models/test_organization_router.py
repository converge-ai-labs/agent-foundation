"""Organization routes and Workspace projections share the real application services."""

import httpx2
import pytest
from a13n_environment import build_environment_provider_catalog
from a13n_service.app import Components, create_app
from a13n_service.iam import AuthenticatedActor
from a13n_service.models.domain import ModelDeclarations
from fastapi import Request

from ..connectivity.connector_helpers import FakeConnectorBackend, fake_registry
from ..resource_scope_helpers import organization_admin
from .conftest import ORG_ID, WORKSPACE_ID, actor
from .test_router import settings

pytestmark = pytest.mark.anyio


async def test_five_configuration_resources_support_org_collections(
    model_sessions, service_database, tmp_path, model_catalog
):
    admin = await organization_admin(model_sessions, actor())
    model_catalog.results[("openai", "openai:gpt-5")] = ModelDeclarations(max_output_tokens=8192)

    async def authenticate(request: Request) -> AuthenticatedActor:
        return admin if request.headers.get("test-scope") == "organization" else actor()

    app = create_app(
        settings(tmp_path, service_database),
        components=Components(
            request_authenticator=authenticate,
            connector_provider_registry=fake_registry(FakeConnectorBackend()),
            environment_provider_catalog=build_environment_provider_catalog(builtin_keys=("a13n.direct-local",)),
            model_catalog=model_catalog,
        ),
    )
    org_path = f"/api/v1/organizations/{ORG_ID}"
    workspace_path = f"/api/v1/workspaces/{WORKSPACE_ID}"
    headers = {"test-scope": "organization", "Idempotency-Key": "org-create"}
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver") as client:
            provider = await client.post(
                f"{org_path}/model-providers",
                headers=headers,
                json={"type": "openai", "name": "Organization OpenAI", "credential": "sk-test"},
            )
            assert provider.status_code == 201, provider.text
            for path, request_headers in ((org_path, headers), (workspace_path, {})):
                suggestion = await client.post(
                    f"{path}/model-catalog/suggestions",
                    headers=request_headers,
                    json={"provider_id": provider.json()["id"], "upstream_model": "gpt-5"},
                )
                assert suggestion.status_code == 200, suggestion.text
                assert suggestion.json()["items"][0]["declarations"]["max_output_tokens"] == 8192
            model = await client.post(
                f"{org_path}/models",
                headers=headers,
                json={
                    "provider_id": provider.json()["id"],
                    "name": "Coding",
                    "key": "coding",
                    "upstream_model": "gpt-5",
                },
            )
            assert model.status_code == 201, model.text
            assert model.json()["base_model"] == "openai:gpt-5"
            assert model.json()["model_api"] == "openai.responses"
            environment_provider = await client.post(
                f"{org_path}/environment-providers",
                headers=headers,
                json={"type": "a13n.direct-local", "name": "Organization local"},
            )
            assert environment_provider.status_code == 201, environment_provider.text
            template = await client.post(
                f"{org_path}/environment-templates",
                headers=headers,
                json={
                    "name": "Standard",
                    "provider_id": environment_provider.json()["id"],
                    "configuration": {"root": {"path": str(tmp_path)}},
                    "retention": {"idle": {"stop_after": None, "delete_after": None}},
                },
            )
            assert template.status_code == 201, template.text
            connector = await client.post(
                f"{org_path}/connector-providers",
                headers=headers,
                json={
                    "type": "fake_connector",
                    "name": "Shared",
                    "configuration": {"tenant": "tenant-1", "endpoint": "https://connector.example"},
                    "credentials": {"api_key": "secret"},
                },
            )
            assert connector.status_code == 201, connector.text
            for collection in [
                "model-providers",
                "models",
                "environment-providers",
                "environment-templates",
                "connector-providers",
            ]:
                org = await client.get(f"{org_path}/{collection}", headers=headers)
                workspace = await client.get(f"{workspace_path}/{collection}")
                assert org.status_code == workspace.status_code == 200
                assert org.json()["items"] == workspace.json()["items"]
                assert org.json()["items"][0]["workspace_id"] is None
                assert (await client.get(f"{org_path}/{collection}")).status_code == 404
                assert (
                    await client.get(f"/api/v1/organizations/org_other/{collection}", headers=headers)
                ).status_code == 404
            collision = await client.post(
                f"{workspace_path}/models",
                json={
                    "provider_id": provider.json()["id"],
                    "name": "Collision",
                    "key": "coding",
                    "upstream_model": "gpt-test",
                    "model_api": "openai.responses",
                },
            )
            assert collision.status_code == 409
