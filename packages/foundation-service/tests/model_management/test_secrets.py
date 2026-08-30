from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_service.database.metadata import service_metadata
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.model_management.domain import PrincipalRef, WorkspaceSecretCredential
from a13n_service.model_management.secrets import DatabaseSecretValueResolver, SecretResolutionError
from a13n_service.secret_management import SecretProtectionError, SecretProtector
from a13n_service.secret_management.models import ManagedSecretRecord
from a13n_service.storage import transaction
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy.ext.asyncio import AsyncEngine

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
USER_ID = "usr_1234567890abcdef"
SECRET_ID = "sec_1234567890abcdef"
MASTER_KEY = b"k" * 32


def protector() -> SecretProtector:
    return SecretProtector(key=MASTER_KEY, encryption_key_id="master-2026-08")


def test_secret_protector_round_trips_and_binds_all_context_fields() -> None:
    protected = protector().encrypt(
        "opaque-value",
        secret_id=SECRET_ID,
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        owner_type="workspace",
        owner_id=WORKSPACE_ID,
        key="openai_api_key",
        version=1,
    )

    assert (
        protector().decrypt(
            ciphertext=protected.ciphertext,
            nonce=protected.nonce,
            encryption_key_id=protected.encryption_key_id,
            secret_id=SECRET_ID,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            owner_type="workspace",
            owner_id=WORKSPACE_ID,
            key="openai_api_key",
            version=1,
        )
        == "opaque-value"
    )
    with pytest.raises(SecretProtectionError, match="authenticated"):
        protector().decrypt(
            ciphertext=protected.ciphertext,
            nonce=protected.nonce,
            encryption_key_id=protected.encryption_key_id,
            secret_id=SECRET_ID,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            owner_type="workspace",
            owner_id=WORKSPACE_ID,
            key="different-key",
            version=1,
        )


@pytest.fixture
async def encrypted_database(tmp_path: Path) -> AsyncIterator[tuple[DatabaseSecretValueResolver, AsyncEngine]]:
    engine = create_sql_engine(SQLiteConfig(path=tmp_path / "secrets.sqlite3"))
    async with engine.begin() as connection:
        await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    protected = protector().encrypt(
        "runtime-api-key",
        secret_id=SECRET_ID,
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        owner_type="workspace",
        owner_id=WORKSPACE_ID,
        key="openai_api_key",
        version=1,
    )
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
        session.add(
            UserRecord(
                id=USER_ID,
                email="runner@example.com",
                normalized_email="runner@example.com",
                name="Runner",
                status="active",
                email_verified_at=NOW,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(
            RoleBindingRecord(
                id="rb_member1234567890",
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
            )
        )
        session.add(
            ManagedSecretRecord(
                id=SECRET_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                owner_type="workspace",
                owner_id=WORKSPACE_ID,
                key="openai_api_key",
                version=1,
                ciphertext=protected.ciphertext,
                nonce=protected.nonce,
                encryption_key_id=protected.encryption_key_id,
                created_at=NOW,
                value_updated_at=NOW,
                deleted_at=None,
            )
        )
    try:
        yield DatabaseSecretValueResolver(sessions, protector()), engine
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_runtime_secret_resolution_closes_session_before_decrypting(
    encrypted_database: tuple[DatabaseSecretValueResolver, AsyncEngine],
) -> None:
    resolver, _ = encrypted_database

    value = await resolver.resolve(
        principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        credential=WorkspaceSecretCredential(secret_id=SECRET_ID),
    )

    assert value == "runtime-api-key"


@pytest.mark.anyio
async def test_runtime_secret_resolution_fails_closed_after_access_revocation(
    encrypted_database: tuple[DatabaseSecretValueResolver, AsyncEngine],
) -> None:
    resolver, engine = encrypted_database
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        user = await session.get(UserRecord, USER_ID)
        assert user is not None
        user.status = "disabled"

    with pytest.raises(SecretResolutionError, match="unavailable"):
        await resolver.resolve(
            principal=PrincipalRef(principal_type="user", principal_id=USER_ID),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            credential=WorkspaceSecretCredential(secret_id=SECRET_ID),
        )
