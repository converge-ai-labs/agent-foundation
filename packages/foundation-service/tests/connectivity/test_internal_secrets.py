from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_service.database import DatabaseMigrator
from a13n_service.iam.models import OrganizationRecord, WorkspaceRecord
from a13n_service.secrets import (
    InternalSecretError,
    InternalSecretService,
    SecretOperation,
    SecretOwnerType,
    SecretProtector,
    SecretUseContext,
)
from a13n_service.storage import transaction
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine

NOW = datetime(2026, 9, 3, 5, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
INGRESS_ID = "ing_1234567890abcdef"


@pytest.fixture
async def internal_secrets(tmp_path: Path) -> AsyncIterator[InternalSecretService]:
    config = SQLiteConfig(path=tmp_path / "internal-secrets.sqlite3")
    DatabaseMigrator(config).upgrade()
    engine = create_sql_engine(config)
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
    try:
        yield InternalSecretService(
            sessions,
            SecretProtector(key=b"k" * 32, encryption_key_id="master-2026-09"),
            clock=lambda: NOW,
        )
    finally:
        await engine.dispose()


def context(
    generation: int,
    operation: SecretOperation = SecretOperation.management,
) -> SecretUseContext:
    return SecretUseContext(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        owner_type=SecretOwnerType.ingress,
        owner_id=INGRESS_ID,
        key="signing_secret",
        operation=operation,
        credential_generation=generation,
    )


@pytest.mark.anyio
async def test_internal_secret_lifecycle_is_generation_bound(
    internal_secrets: InternalSecretService,
) -> None:
    created = await internal_secrets.create(context(1), "first-value")

    assert created.version == 1
    assert await internal_secrets.resolve(context(1, SecretOperation.runtime)) == "first-value"
    replaced = await internal_secrets.replace(context(1), "second-value")
    assert replaced.secret_id == created.secret_id
    assert replaced.version == 2
    with pytest.raises(InternalSecretError, match="unavailable"):
        await internal_secrets.resolve(context(1, SecretOperation.runtime))
    assert await internal_secrets.resolve(context(2, SecretOperation.runtime)) == "second-value"

    await internal_secrets.tombstone(context(2))
    with pytest.raises(InternalSecretError, match="unavailable"):
        await internal_secrets.resolve(context(2, SecretOperation.runtime))
    with pytest.raises(InternalSecretError, match="already exists"):
        await internal_secrets.create(context(1), "resurrected-value")


@pytest.mark.anyio
async def test_internal_secret_rejects_duplicate_and_owner_mismatch(
    internal_secrets: InternalSecretService,
) -> None:
    await internal_secrets.create(context(1), "first-value")
    with pytest.raises(InternalSecretError, match="cannot read"):
        await internal_secrets.resolve(context(1))
    with pytest.raises(InternalSecretError, match="already exists"):
        await internal_secrets.create(context(1), "duplicate")

    invalid_owner = SecretUseContext(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        owner_type=SecretOwnerType.workspace,
        owner_id="ws_aaaaaaaaaaaaaaaa",
        key="credential_bundle",
        operation=SecretOperation.management,
        credential_generation=1,
    )
    with pytest.raises(InternalSecretError, match="must match"):
        await internal_secrets.create(invalid_owner, "value")
