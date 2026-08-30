from __future__ import annotations

from base64 import b64encode
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from a13n_service.app import ServiceComponents, create_app
from a13n_service.connectors import (
    ConnectorProvider,
    ConnectorProviderCapabilities,
    ConnectorProviderCatalog,
    ConnectorProviderMetadata,
    ConnectorProviderRegistration,
)
from a13n_service.database.metadata import service_metadata
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.settings import ServiceSettings
from a13n_service.storage import transaction
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from fastapi import Request

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
USER_ID = "usr_1234567890abcdef"


class _Provider(ConnectorProvider):
    @property
    def metadata(self) -> ConnectorProviderMetadata:
        return ConnectorProviderMetadata(
            display_name="HTTP API",
            description="Test Connector Provider",
            contract_version="1",
            provider_config_schemas={
                "1": {
                    "type": "object",
                    "properties": {"base_url": {"type": "string"}},
                    "required": ["base_url"],
                    "additionalProperties": False,
                }
            },
            capabilities=ConnectorProviderCapabilities(),
        )

    def validate_config(self, provider_config_version: str, config) -> None:
        if provider_config_version != "1":
            raise ValueError("unsupported")


def _catalog() -> ConnectorProviderCatalog:
    provider = _Provider()
    registration = ConnectorProviderRegistration(
        provider_key="http_api",
        class_module=__name__,
        class_qualname="_Provider",
        import_target="tests:_Provider",
        distribution_name="a13n-connector-http-api",
        distribution_version="1.0.0",
        metadata=provider.metadata,
    )
    return ConnectorProviderCatalog(((registration, provider),))


async def _authenticate(request: Request) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
        auth_method="session",
        credential_id="ses_1234567890abcdef",
        boundary_workspace_id=WORKSPACE_ID,
        request_id=request.state.request_id,
    )


def _settings(tmp_path: Path) -> ServiceSettings:
    return ServiceSettings(
        _env_file=None,
        database_backend="sqlite",
        database_sqlite_path=tmp_path / "connector-api.sqlite3",
        redis_backend="memory",
        object_backend="local",
        object_local_root=tmp_path / "objects",
        filesystem_root=tmp_path / "files",
        model_resolve_dns_on_save=False,
        secret_master_key_base64=b64encode(b"0123456789abcdef0123456789abcdef").decode(),
        secret_encryption_key_id="connector-api-test-key",
    )


async def _seed(config: ServiceSettings) -> None:
    engine = create_sql_engine(config.database_config())
    async with engine.begin() as connection:
        await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        session.add(OrganizationRecord(id=ORG_ID, name="Test", version=1, created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Default",
                normalized_name="default",
                version=1,
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
                version=1,
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
    config = _settings(tmp_path)
    await _seed(config)
    app = create_app(
        config,
        components=ServiceComponents(
            request_authenticator=_authenticate,
            connector_provider_catalog=_catalog(),
        ),
    )
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.mark.anyio
async def test_connector_management_http_lifecycle(api_client: httpx2.AsyncClient) -> None:
    providers = await api_client.get("/api/v1/connector-providers")
    assert providers.status_code == 200
    assert providers.json()["items"][0]["key"] == "http_api"
    assert providers.json()["items"][0]["contract_version"] == "1"

    created = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/connectors",
        json={
            "name": "Internal API",
            "provider_key": "http_api",
            "provider_config_version": "1",
            "config": {"base_url": "https://api.example.test"},
        },
    )
    assert created.status_code == 201
    connector_id = created.json()["connector"]["id"]
    first_revision_id = created.json()["revision"]["id"]

    fetched = await api_client.get(f"/api/v1/connectors/{connector_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Internal API"

    patched = await api_client.patch(
        f"/api/v1/connectors/{connector_id}",
        json={"name": "Public API", "expected_version": 1},
    )
    assert patched.status_code == 200
    assert patched.json()["version"] == 2

    revision = await api_client.post(
        f"/api/v1/connectors/{connector_id}/revisions",
        json={
            "provider_key": "http_api",
            "provider_config_version": "1",
            "config": {"base_url": "https://api-v2.example.test"},
        },
    )
    assert revision.status_code == 201
    assert revision.json()["revision"]["version"] == 2

    revisions = await api_client.get(f"/api/v1/connectors/{connector_id}/revisions")
    assert revisions.status_code == 200
    assert [item["version"] for item in revisions.json()["items"]] == [2, 1]

    first_revision = await api_client.get(f"/api/v1/connector-revisions/{first_revision_id}")
    assert first_revision.status_code == 200
    assert first_revision.json()["version"] == 1


@pytest.mark.anyio
async def test_connector_http_errors_are_stable_and_safe(api_client: httpx2.AsyncClient) -> None:
    missing = await api_client.get("/api/v1/connectors/con_0000000000000000")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    invalid = await api_client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/connectors",
        json={
            "name": "Invalid",
            "provider_key": "http_api",
            "provider_config_version": "1",
            "config": {},
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "provider_config_incompatible"
