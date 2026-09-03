from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_service.agents.models import AgentRecord
from a13n_service.connectivity.composition import AdapterDefinition, AdapterRegistry
from a13n_service.connectivity.ingress.routes import RouteService
from a13n_service.connectivity.ingress.service import IngressService
from a13n_service.database.metadata import service_metadata
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import (
    OrganizationRecord,
    RoleBindingRecord,
    ServiceAccountRecord,
    UserRecord,
    WorkspaceRecord,
)
from a13n_service.secrets import InternalSecretService, SecretProtector
from a13n_service.storage import transaction
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

NOW = datetime(2026, 9, 3, 8, 0, tzinfo=UTC)
ORG_ID = "org_abcdef1234567890"
WORKSPACE_ID = "ws_abcdef1234567890"
USER_ID = "usr_abcdef1234567890"
SERVICE_ACCOUNT_ID = "sa_abcdef1234567890"
AGENT_ID = "agt_abcdef1234567890"


class FakeIngressAdapter:
    provider_key = "fake"
    config_versions = frozenset({"fake_http_v1"})
    allows_runtime_ambiguity = False

    def validate_config(self, value: object, *, config_version: str) -> dict[str, object]:
        if config_version != "fake_http_v1" or not isinstance(value, dict) or set(value) != {"installation_id"}:
            raise ValueError("invalid config")
        installation_id = value["installation_id"]
        if not isinstance(installation_id, str) or not installation_id:
            raise ValueError("invalid installation")
        return {"installation_id": installation_id}

    def validate_credentials(self, value: dict[str, str], *, config_version: str) -> dict[str, object]:
        if config_version != "fake_http_v1" or set(value) != {"token"} or not value["token"]:
            raise ValueError("invalid credentials")
        return dict(value)

    def configuration_identity(self, value: dict[str, object], *, config_version: str) -> object:
        del config_version
        return value["installation_id"]

    def validate_route(
        self,
        *,
        match: object,
        provider_policy: object,
        ingress_config: dict[str, object],
        config_version: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        del ingress_config, config_version
        if not isinstance(match, dict) or set(match) != {"channel"} or not isinstance(match["channel"], str):
            raise ValueError("invalid match")
        if provider_policy != {}:
            raise ValueError("invalid policy")
        return dict(match), {}

    def prove_non_overlap(self, left: dict[str, object], right: dict[str, object]) -> bool | None:
        return left["channel"] != right["channel"]


def actor() -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
        auth_method="session",
        credential_id="ses_connectivity_test",
        boundary_workspace_id=WORKSPACE_ID,
        request_id="req-connectivity-test",
    )


def adapter_registry() -> AdapterRegistry[FakeIngressAdapter]:
    return AdapterRegistry(
        (
            AdapterDefinition(
                key="fake",
                config_versions=frozenset({"fake_http_v1"}),
                factory=FakeIngressAdapter,
            ),
        )
    )


@pytest.fixture
async def connectivity_sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_sql_engine(SQLiteConfig(path=tmp_path / "connectivity.sqlite3"))
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
                email="connectivity@example.com",
                normalized_email="connectivity@example.com",
                name="Connectivity Admin",
                status="active",
                email_verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            ServiceAccountRecord(
                id=SERVICE_ACCOUNT_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                name="Ingress Runner",
                normalized_name="ingress runner",
                description=None,
                status="active",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.flush()
        session.add(
            AgentRecord(
                id=AGENT_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                source="custom",
                name="Support",
                normalized_name="support",
                description=None,
                version=1,
                current_revision_id="agtr_connectivity_test",
                enabled=True,
                archived_at=None,
                duplicated_from_agent_id=None,
                duplicated_from_revision_id=None,
                created_by_type="user",
                created_by_id=USER_ID,
                updated_by_type="user",
                updated_by_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add_all(
            (
                RoleBindingRecord(
                    id="rb_connectivity_org",
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
                    id="rb_connectivity_admin",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=USER_ID,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key="admin",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_connectivity_runner",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="service_account",
                    principal_id=SERVICE_ACCOUNT_ID,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key="runner",
                    created_by_user_id=USER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.fixture
def ingress_service(connectivity_sessions: async_sessionmaker[AsyncSession]) -> IngressService:
    secrets = InternalSecretService(
        connectivity_sessions,
        SecretProtector(key=b"k" * 32, encryption_key_id="connectivity-test"),
        clock=lambda: NOW,
    )
    return IngressService(
        connectivity_sessions,
        adapter_registry(),
        secrets,
        clock=lambda: NOW,
    )


@pytest.fixture
def route_service(connectivity_sessions: async_sessionmaker[AsyncSession]) -> RouteService:
    return RouteService(
        connectivity_sessions,
        adapter_registry(),
        batch_max_events=100,
        batch_max_wait_seconds=300,
        clock=lambda: NOW,
    )
