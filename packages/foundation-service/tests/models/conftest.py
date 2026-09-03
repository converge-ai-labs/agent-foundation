from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_service.database.metadata import service_metadata
from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.models.endpoint_policy import EndpointPolicy
from a13n_service.models.provider_service import ModelProviderService
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.service import ModelService
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.storage import transaction
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

NOW = datetime(2026, 9, 3, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
USER_ID = "usr_1234567890abcdef"


def actor() -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
        auth_method="session",
        credential_id="ses_1234567890abcdef",
        boundary_workspace_id=WORKSPACE_ID,
        request_id="req-model-test",
    )


def protector() -> SecretProtector:
    return SecretProtector.from_base64(
        encoded_key=base64.b64encode(b"m" * 32).decode(),
        encryption_key_id="test-master",
    )


@pytest.fixture
async def model_sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_sql_engine(SQLiteConfig(path=tmp_path / "models.sqlite3"))
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
    try:
        yield sessions
    finally:
        await engine.dispose()


@pytest.fixture
def provider_service(model_sessions: async_sessionmaker[AsyncSession]) -> ModelProviderService:
    return ModelProviderService(
        model_sessions,
        built_in_provider_registry(),
        EndpointPolicy.from_operator_allowlist(private_domains=(), private_cidrs=()),
        protector(),
        clock=lambda: NOW,
        resolve_dns_on_save=False,
    )


@pytest.fixture
def model_service(model_sessions: async_sessionmaker[AsyncSession]) -> ModelService:
    return ModelService(model_sessions, built_in_provider_registry(), clock=lambda: NOW)
