from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_service.connectors import ConnectorProviderSecret, DatabaseConnectorSecretStore
from a13n_service.database import DatabaseMigrator
from a13n_service.iam.domain import PrincipalRef
from a13n_service.iam.models import OrganizationRecord, WorkspaceRecord
from a13n_service.secret_management import SecretProtector
from a13n_service.secret_management.models import ManagedSecretRecord
from a13n_service.storage import transaction
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

ORG_ID = "org_0000000000000001"
WORKSPACE_ID = "ws_0000000000000001"
USER_ID = "usr_0000000000000001"
CONNECTION_ID = "conn_0000000000000001"


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    config = SQLiteConfig(path=tmp_path / "foundation.sqlite3")
    DatabaseMigrator(config).upgrade()
    engine = create_sql_engine(config)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    async with transaction(factory) as session:
        session.add(
            OrganizationRecord(
                id=ORG_ID,
                name="Test Organization",
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Test Workspace",
                normalized_name="test-workspace",
                version=1,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
        )
    try:
        yield factory
    finally:
        await engine.dispose()


def _actor() -> PrincipalRef:
    return PrincipalRef(principal_type="user", principal_id=USER_ID)


@pytest.mark.anyio
async def test_connection_secrets_use_canonical_encrypted_secret_rows(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = DatabaseConnectorSecretStore(
        sessions,
        SecretProtector(key=b"k" * 32, encryption_key_id="test-key"),
    )
    async with transaction(sessions) as session:
        await store.replace_connection_secrets(
            session,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connection_id=CONNECTION_ID,
            secrets=(
                ConnectorProviderSecret(key="access_token", value=SecretStr("first-token")),
                ConnectorProviderSecret(key="refresh_token", value=SecretStr("refresh-token")),
            ),
            actor=_actor(),
        )

    values = await store.read_connection_secrets(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connection_id=CONNECTION_ID,
    )
    assert {item.key: item.value.get_secret_value() for item in values} == {
        "access_token": "first-token",
        "refresh_token": "refresh-token",
    }
    async with sessions() as session:
        records = tuple((await session.scalars(select(ManagedSecretRecord))).all())
    assert all(record.ciphertext not in {b"first-token", b"refresh-token"} for record in records)
    assert all(record.owner_type == "connection" for record in records)

    async with transaction(sessions) as session:
        await store.replace_connection_secrets(
            session,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connection_id=CONNECTION_ID,
            secrets=(ConnectorProviderSecret(key="access_token", value=SecretStr("rotated-token")),),
            actor=_actor(),
        )

    rotated = await store.read_connection_secrets(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connection_id=CONNECTION_ID,
    )
    assert [(item.key, item.value.get_secret_value()) for item in rotated] == [("access_token", "rotated-token")]

    async with transaction(sessions) as session:
        await store.delete_connection_secrets(
            session,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connection_id=CONNECTION_ID,
            actor=_actor(),
        )
    assert (
        await store.read_connection_secrets(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            connection_id=CONNECTION_ID,
        )
        == ()
    )
