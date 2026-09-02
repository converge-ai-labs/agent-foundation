from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_environment_provider import build_environment_provider_catalog
from a13n_service.agents.environment_resolution import AgentEnvironmentSelectionResolver
from a13n_service.database.metadata import service_metadata
from a13n_service.environments.catalog import FoundationEnvironmentProviderCatalog
from a13n_service.environments.service import EnvironmentManagementService
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import transaction
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
ORG_ID = "org_env1234567890abcd"
WORKSPACE_ID = "ws_env1234567890abcde"
USER_ID = "usr_env1234567890abcd"
SECRET_ID = "sec_env1234567890abcd"


def actor() -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
        auth_method="session",
        credential_id="ses_env1234567890abcd",
        boundary_workspace_id=WORKSPACE_ID,
        request_id="req-environment-test",
    )


@pytest.fixture
async def environment_sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_sql_engine(SQLiteConfig(path=tmp_path / "environments.sqlite3"))
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
                email="environment@example.com",
                normalized_email="environment@example.com",
                name="Environment Builder",
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
                    id="rb_envorg1234567890",
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
                    id="rb_envws12345678901",
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
                    key="environment_token",
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
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.fixture
def provider_catalog() -> FoundationEnvironmentProviderCatalog:
    return FoundationEnvironmentProviderCatalog(build_environment_provider_catalog(builtin_keys=("a13n.direct-local",)))


@pytest.fixture
def environment_service(
    environment_sessions: async_sessionmaker[AsyncSession],
    provider_catalog: FoundationEnvironmentProviderCatalog,
) -> EnvironmentManagementService:
    return EnvironmentManagementService(environment_sessions, provider_catalog, clock=lambda: NOW)


@pytest.fixture
def environment_resolver(
    environment_sessions: async_sessionmaker[AsyncSession],
    provider_catalog: FoundationEnvironmentProviderCatalog,
) -> AgentEnvironmentSelectionResolver:
    return AgentEnvironmentSelectionResolver(environment_sessions, provider_catalog)
