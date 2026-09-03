from base64 import b64encode
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from a13n_service.app import ServiceComponents, create_app
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.models.domain import PrincipalRef
from a13n_service.secrets.models import SecretRecord
from a13n_service.settings import ServiceSettings
from a13n_service.storage import transaction
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from fastapi import Request

NOW = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
USER_ID = "usr_1234567890abcdef"
SECRET_ID = "sec_1234567890abcdef"


async def authenticate(request: Request) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
        auth_method="session",
        credential_id="ses_1234567890abcdef",
        boundary_workspace_id=WORKSPACE_ID,
        request_id=request.state.request_id,
    )


async def successful_test(**_: object) -> None:
    return None


def settings(tmp_path: Path, database_path: Path) -> ServiceSettings:
    return ServiceSettings(
        _env_file=None,
        database_backend="sqlite",
        database_sqlite_path=database_path,
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
                SecretRecord(
                    id=SECRET_ID,
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    owner_type="workspace",
                    owner_id=WORKSPACE_ID,
                    key="openai_api_key",
                    version=1,
                    ciphertext=b"encrypted",
                    nonce=b"123456789012",
                    encryption_key_id="test-key",
                    created_at=NOW,
                    value_updated_at=NOW,
                    deleted_at=None,
                ),
            )
        )
    await engine.dispose()


@pytest.fixture
async def api_client(
    tmp_path: Path,
    service_sqlite_database: Path,
) -> AsyncIterator[httpx2.AsyncClient]:
    config = settings(tmp_path, service_sqlite_database)
    await seed_database(config)
    app = create_app(
        config,
        components=ServiceComponents(
            request_authenticator=authenticate,
            model_connection_tester=successful_test,
        ),
    )
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


def candidate(name: str = "Primary") -> dict[str, object]:
    return {
        "name": name,
        "config": model_config(),
    }


def model_config(model_name: str = "gpt-5.6-terra") -> dict[str, object]:
    return {
        "provider_type": "openai",
        "model_name": model_name,
        "credential": {"source": "workspace_secret", "secret_id": SECRET_ID},
        "provider_config": {},
    }


@pytest.mark.anyio
async def test_provider_discovery_is_authenticated_and_finite(api_client: httpx2.AsyncClient) -> None:
    response = await api_client.get("/api/v1/model-providers")

    assert response.status_code == 200
    assert response.json()["next_cursor"] is None
    assert {item["key"] for item in response.json()["items"]} >= {"openai", "deepseek", "moonshot", "zhipu"}


@pytest.mark.anyio
async def test_model_resource_http_lifecycle(api_client: httpx2.AsyncClient) -> None:
    created = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/models",
        json=candidate(),
    )
    assert created.status_code == 201
    result = created.json()
    model = result["model"]
    revision = result["revision"]
    assert model["version"] == 1
    assert revision["version"] == 1
    assert model["current_revision_id"] == revision["id"]
    model_url = f"/api/v1/workspaces/{WORKSPACE_ID}/models/{model['id']}"

    fetched = await api_client.get(model_url)
    assert fetched.status_code == 200
    assert fetched.json() == model

    no_precondition = await api_client.patch(model_url, json={"enabled": False})
    assert no_precondition.status_code == 400
    stale = await api_client.patch(model_url, json={"enabled": False}, headers={"If-Match": '"stale"'})
    assert stale.status_code == 412
    assert stale.json()["error"]["code"] == "precondition_failed"

    patched = await api_client.patch(
        model_url,
        json={"enabled": False},
        headers={"If-Match": fetched.headers["etag"]},
    )
    assert patched.status_code == 200
    assert not patched.json()["enabled"]
    assert patched.json()["version"] == 1

    revised = await api_client.post(
        f"{model_url}/revisions",
        json={"expected_version": 1, "config": model_config("gpt-5.6-sol")},
    )
    assert revised.status_code == 201
    assert revised.json()["model"]["version"] == 2

    listed = await api_client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/models?enabled=false")
    assert [item["id"] for item in listed.json()["items"]] == [model["id"]]

    tested = await api_client.post(f"/api/v1/workspaces/{WORKSPACE_ID}/models/test", json=model_config())
    assert tested.status_code == 200
    assert tested.json()["success"]

    assert (await api_client.post(f"{model_url}/copy", json={"name": "Copy", "enabled": False})).status_code == 404
    assert (await api_client.get(f"{model_url}/references")).status_code == 404
    assert (await api_client.delete(model_url)).status_code == 404


@pytest.mark.anyio
async def test_unknown_input_fields_use_shared_safe_error(api_client: httpx2.AsyncClient) -> None:
    body = candidate()
    body["api_key"] = "must-not-be-accepted"

    response = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/models",
        json=body,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert "must-not-be-accepted" not in response.text
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]


@pytest.mark.anyio
async def test_missing_authenticator_returns_401(
    tmp_path: Path,
    service_sqlite_database: Path,
) -> None:
    config = settings(tmp_path, service_sqlite_database)
    await seed_database(config)
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/model-providers")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
