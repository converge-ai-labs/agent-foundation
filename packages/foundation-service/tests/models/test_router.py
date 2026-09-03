from base64 import b64encode
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from a13n_service.app import ServiceComponents, create_app
from a13n_service.database.metadata import service_metadata
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.settings import ServiceSettings
from a13n_service.storage import transaction
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from fastapi import Request

NOW = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
USER_ID = "usr_1234567890abcdef"


async def authenticate(request: Request) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
        auth_method="session",
        credential_id="ses_1234567890abcdef",
        boundary_workspace_id=WORKSPACE_ID,
        request_id=request.state.request_id,
    )


async def successful_model_test(**_: object) -> None:
    return None


def settings(tmp_path: Path) -> ServiceSettings:
    return ServiceSettings(
        _env_file=None,
        database_backend="sqlite",
        database_sqlite_path=tmp_path / "api.sqlite3",
        redis_backend="memory",
        object_backend="local",
        object_local_root=tmp_path / "objects",
        filesystem_root=tmp_path / "files",
        model_resolve_dns_on_save=False,
        secret_master_key_base64=b64encode(b"0123456789abcdef0123456789abcdef").decode(),
        secret_encryption_key_id="model-management-test-key",
    )


async def seed_database(config: ServiceSettings) -> None:
    engine = create_sql_engine(config.database_config())
    async with engine.begin() as connection:
        await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        session.add(OrganizationRecord(id=ORG_ID, name="Test", created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Default",
                normalized_name="default",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.flush()
        session.add(
            UserRecord(
                id=USER_ID,
                email="builder@example.com",
                normalized_email="builder@example.com",
                name="Builder",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add_all(
            (
                RoleBindingRecord(
                    id="rb_org1234567890abcd",
                    organization_id=ORG_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="organization",
                    resource_id=ORG_ID,
                    role_key="member",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_ws1234567890abcde",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key="builder",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )
    await engine.dispose()


@pytest.fixture
async def api_client(tmp_path: Path) -> AsyncIterator[httpx2.AsyncClient]:
    config = settings(tmp_path)
    await seed_database(config)
    app = create_app(
        config,
        components=ServiceComponents(
            request_authenticator=authenticate,
            model_connection_tester=successful_model_test,
        ),
    )
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


async def create_provider(api_client: httpx2.AsyncClient, name: str = "OpenAI Primary") -> dict[str, object]:
    response = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/model-providers",
        json={"type": "openai", "name": name, "credential": "sk-secret"},
    )
    assert response.status_code == 201
    assert "sk-secret" not in response.text
    return response.json()


@pytest.mark.anyio
async def test_provider_type_and_multiple_provider_http_lifecycle(api_client: httpx2.AsyncClient) -> None:
    definitions = await api_client.get("/api/v1/model-provider-types")
    first = await create_provider(api_client, "OpenAI Production")
    second = await create_provider(api_client, "OpenAI Personal")

    assert definitions.status_code == 200
    assert {item["key"] for item in definitions.json()["items"]} >= {"openai", "openrouter", "ollama"}
    assert first["type"] == second["type"] == "openai"
    assert first["id"] != second["id"]
    assert first["credential_configured"] and "credential" not in first

    listed = await api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/model-providers?provider_type=openai")
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()["items"]} == {first["id"], second["id"]}


@pytest.mark.anyio
async def test_model_http_lifecycle_has_no_revision_or_default_api(api_client: httpx2.AsyncClient) -> None:
    provider = await create_provider(api_client)
    created = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/models",
        json={
            "key": "support/main",
            "provider_id": provider["id"],
            "name": "Support",
            "upstream_model": "gpt-current",
            "model_apis": [
                {"api": "openai.responses", "profile": {"supports_tools": True}},
                {"api": "openai.chat_completions"},
            ],
        },
    )
    assert created.status_code == 201
    model = created.json()
    assert model["key"] == "support/main"
    assert model["provider_id"] == provider["id"]
    assert "version" not in model and "current_revision_id" not in model
    assert "default_model_api" not in model
    model_url = f"/api/v1/workspaces/{WORKSPACE_ID}/models/{model['id']}"

    fetched = await api_client.get(model_url)
    assert fetched.status_code == 200
    assert fetched.json() == model

    stale = await api_client.patch(model_url, json={"enabled": False}, headers={"If-Match": '"stale"'})
    assert stale.status_code == 412
    patched = await api_client.patch(
        model_url,
        json={"upstream_model": "gpt-new"},
        headers={"If-Match": fetched.headers["etag"]},
    )
    assert patched.status_code == 200
    assert patched.json()["upstream_model"] == "gpt-new"
    assert patched.json()["key"] == model["key"]
    assert patched.json()["provider_id"] == provider["id"]

    tested = await api_client.post(f"{model_url}/test", json={"model_api": "openai.responses"})
    assert tested.status_code == 200
    assert tested.json()["success"]
    assert (await api_client.get(f"{model_url}/revisions")).status_code == 404


@pytest.mark.anyio
async def test_unknown_fields_use_shared_safe_error(api_client: httpx2.AsyncClient) -> None:
    response = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/model-providers",
        json={"type": "openai", "name": "OpenAI", "credential": "must-not-leak", "api_key": "must-not-leak"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert "must-not-leak" not in response.text
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]


@pytest.mark.anyio
async def test_missing_authenticator_returns_401(tmp_path: Path) -> None:
    config = settings(tmp_path)
    await seed_database(config)
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/model-provider-types")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
