from __future__ import annotations

from base64 import b64encode
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from a13n_service.app import Components, create_app
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.settings import Settings
from a13n_service.storage import transaction
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from fastapi import Request

from .conftest import ORG_ID, USER_ID, WORKSPACE_ID

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
PROVIDER_KEY = "a13n.direct-local"


async def authenticate(request: Request) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
        auth_method="session",
        credential_id="ses_envrouter12345678",
        boundary_workspace_id=WORKSPACE_ID,
        request_id=request.state.request_id,
    )


def settings(tmp_path: Path, database_path: Path) -> Settings:
    return Settings(
        database={"backend": "sqlite", "sqlite_path": database_path},
        redis={"backend": "memory"},
        objects={"backend": "local", "local_root": tmp_path / "objects"},
        filesystem={"root": tmp_path / "files"},
        environments={"provider_builtins": (PROVIDER_KEY,)},
        models={"resolve_dns_on_save": False},
        secrets={
            "master_key_base64": b64encode(b"0123456789abcdef0123456789abcdef").decode(),
            "encryption_key_id": "environment-router-test-key",
        },
        connectivity={"public_origin": "http://testserver", "http_origins": ("http://testserver",)},
    )


async def seed_database(config: Settings) -> None:
    engine = create_sql_engine(config.database_config())
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        session.add(OrganizationRecord(id=ORG_ID, key="test", name="Test", created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Default",
                key="default",
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
        components=Components(
            request_authenticator=authenticate,
        ),
    )
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


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


@pytest.mark.anyio
async def test_template_and_empty_thread_http_contract(environment_api_client, tmp_path):
    client = environment_api_client
    types = await client.get("/api/v1/environment-provider-types")
    assert types.status_code == 200
    assert types.json()["items"][0]["type"] == PROVIDER_KEY
    base = f"/api/v1/workspaces/{WORKSPACE_ID}"
    response = await client.post(f"{base}/environment-providers", json={"type": PROVIDER_KEY, "name": "Local"})
    assert response.status_code == 201, response.text
    provider = response.json()
    assert "credential" not in provider and not provider["credential_configured"]
    response = await client.post(
        f"{base}/environment-templates",
        headers={"Idempotency-Key": "template"},
        json={
            "name": "Workspace",
            "provider_id": provider["id"],
            "configuration": {"root": {"path": str(tmp_path / "absent")}},
            "retention": {"idle": {"stop_after": None, "delete_after": None}},
            "preparation": "on_use",
        },
    )
    assert response.status_code == 201, response.text
    template = response.json()
    response = await client.post(
        f"{base}/threads", headers={"Idempotency-Key": "thread"}, json={"environment": {"template_id": template["id"]}}
    )
    assert response.status_code == 201, response.text
    thread = response.json()
    assert thread["current_run_id"] is None and thread["default_environment_id"]
    environment = await client.get(f"/api/v1/environments/{thread['default_environment_id']}")
    assert environment.status_code == 200 and environment.json()["status"] == "unprepared"
    assert "state" not in environment.json()
    assert not (tmp_path / "absent").exists()
    assert (await client.post(f"/api/v1/environments/{thread['default_environment_id']}/test")).status_code == 404
