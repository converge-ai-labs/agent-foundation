from __future__ import annotations

from base64 import b64encode
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from a13n_service.app import ServiceComponents, create_app
from a13n_service.environments.domain import EnvironmentRevision
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.settings import ServiceSettings
from a13n_service.storage import transaction
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from fastapi import Request

from .conftest import ORG_ID, USER_ID, WORKSPACE_ID

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
PROVIDER_KEY = "a13n.direct-local"


async def attachment_test(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    revision: EnvironmentRevision,
) -> None:
    del actor, organization_id, workspace_id, revision


async def authenticate(request: Request) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
        auth_method="session",
        credential_id="ses_envrouter12345678",
        boundary_workspace_id=WORKSPACE_ID,
        request_id=request.state.request_id,
    )


def settings(tmp_path: Path, database_path: Path) -> ServiceSettings:
    return ServiceSettings(
        _env_file=None,
        database_backend="sqlite",
        database_sqlite_path=database_path,
        redis_backend="memory",
        object_backend="local",
        object_local_root=tmp_path / "objects",
        filesystem_root=tmp_path / "files",
        environment_provider_builtins=(PROVIDER_KEY,),
        model_resolve_dns_on_save=False,
        secret_master_key_base64=b64encode(b"0123456789abcdef0123456789abcdef").decode(),
        secret_encryption_key_id="environment-router-test-key",
        connectivity_public_origin="http://testserver",
        connectivity_http_origins=("http://testserver",),
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
                email="router@example.com",
                normalized_email="router@example.com",
                name="Router Builder",
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
                    id="rb_envrouterorg12345",
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
                    id="rb_envrouterws123456",
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
async def environment_api_client(
    tmp_path: Path,
    service_sqlite_database: Path,
) -> AsyncIterator[httpx2.AsyncClient]:
    config = settings(tmp_path, service_sqlite_database)
    await seed_database(config)
    app = create_app(
        config,
        components=ServiceComponents(
            request_authenticator=authenticate,
            environment_attachment_tester=attachment_test,
        ),
    )
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.mark.anyio
async def test_environment_management_http_lifecycle(
    environment_api_client: httpx2.AsyncClient,
    tmp_path: Path,
) -> None:
    catalog = await environment_api_client.get("/api/v1/environment-providers")
    assert catalog.status_code == 200
    assert [item["provider_key"] for item in catalog.json()["items"]] == [PROVIDER_KEY]

    selection_url = f"/api/v1/workspaces/{WORKSPACE_ID}/environment-providers/{PROVIDER_KEY}"
    selected = await environment_api_client.put(selection_url, json={"enabled": True})
    assert selected.status_code == 200
    assert "etag" in selected.headers

    create_body = {
        "name": "Local",
        "connection": {
            "provider_key": PROVIDER_KEY,
            "schema_version": "1",
            "parameters": {
                "environment_id": "router-local",
                "root": {"path": str(tmp_path)},
            },
        },
        "credential_bindings": [],
        "access": "full",
    }
    collection_url = f"/api/v1/workspaces/{WORKSPACE_ID}/environments"
    created = await environment_api_client.post(
        collection_url,
        headers={"Idempotency-Key": "create-router-environment"},
        json=create_body,
    )
    assert created.status_code == 201
    environment = created.json()
    fetched = await environment_api_client.get(f"/api/v1/environments/{environment['id']}")
    assert fetched.json() == environment

    listed = await environment_api_client.get(collection_url)
    assert [item["id"] for item in listed.json()["items"]] == [environment["id"]]

    revisions_url = f"/api/v1/environments/{environment['id']}/revisions"
    revisions = await environment_api_client.get(revisions_url)
    assert revisions.status_code == 200
    assert revisions.json()["items"][0]["id"] == environment["current_revision_id"]
    assert "connection" not in revisions.json()["items"][0]

    revision_url = f"/api/v1/environment-revisions/{environment['current_revision_id']}"
    detail = await environment_api_client.get(revision_url)
    assert detail.status_code == 200
    assert detail.json()["connection"]["provider_key"] == PROVIDER_KEY
    assert detail.json()["connection"]["parameters"]["environment_id"] == "router-local"
    assert detail.json()["connection"]["parameters"]["root"]["path"] == str(tmp_path)
    assert detail.headers["cache-control"] == "private, no-store"

    tested = await environment_api_client.post(f"{revision_url}/test")
    assert tested.status_code == 200
    assert tested.json() == {"success": True, "code": "attachment_ready"}
    assert tested.headers["cache-control"] == "private, no-store"

    no_op = await environment_api_client.post(
        revisions_url,
        headers={"Idempotency-Key": "router-revision-noop"},
        json={
            "expected_version": 1,
            "connection": create_body["connection"],
            "credential_bindings": [],
            "access": "full",
        },
    )
    assert no_op.status_code == 200

    archived = await environment_api_client.patch(
        f"/api/v1/environments/{environment['id']}",
        headers={"If-Match": fetched.headers["etag"]},
        json={"archived": True},
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None


@pytest.mark.anyio
async def test_environment_create_requires_idempotency_key(
    environment_api_client: httpx2.AsyncClient,
) -> None:
    response = await environment_api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/environments",
        json={},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
