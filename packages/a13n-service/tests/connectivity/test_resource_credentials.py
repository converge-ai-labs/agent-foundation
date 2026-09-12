"""Owner and organization authentication applies to material on each resource record."""

from dataclasses import replace

import pytest
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.connectors.models import ConnectorProviderRecord
from a13n_service.connectivity.mcp.models import MCPAuthorizationRecord, MCPConnectionRecord
from a13n_service.credentials import ResourceCredential
from a13n_service.models.models import ModelProviderRecord
from a13n_service.secrets import SecretProtectionError, SecretProtector
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import transaction
from sqlalchemy import func, select

from .conftest import ACCOUNT_ID, ORG_ID, WORKSPACE_ID


@pytest.mark.parametrize(
    "record_type",
    [ModelProviderRecord, ConnectorProviderRecord, AccountRecord, MCPConnectionRecord, MCPAuthorizationRecord],
)
def test_resource_material_authenticates_owner_organization_generation_and_key(
    record_type: type[ResourceCredential],
) -> None:
    protector = SecretProtector(key=b"k" * 32, encryption_key_id="test")
    record = record_type()
    record.id = "resource-one"
    record.organization_id = ORG_ID
    record.workspace_id = WORKSPACE_ID
    record.credential_generation = 0
    if isinstance(record, MCPAuthorizationRecord):
        record.connection_id = "connection-one"
    record.replace_credential("first-secret", protector)
    encrypted = record.credential_snapshot()
    assert encrypted.decrypt(protector) == "first-secret"
    assert b"first-secret" not in encrypted.ciphertext
    for field, value in (
        ("resource_id", "another-resource"),
        ("owner_id", "another-owner"),
        ("owner_type", "another-kind"),
        ("organization_id", "another-organization"),
        ("workspace_id", "another-workspace"),
        ("generation", 2),
        ("encryption_key_id", "another-key"),
    ):
        with pytest.raises(SecretProtectionError):
            replace(encrypted, **{field: value}).decrypt(protector)
    with pytest.raises(SecretProtectionError):
        encrypted.decrypt(SecretProtector(key=b"x" * 32, encryption_key_id="test"))
    record.replace_credential("second-secret", protector)
    assert record.credential_generation == 2
    assert record.nonce != encrypted.nonce
    assert record.credential_snapshot().decrypt(protector) == "second-secret"
    with pytest.raises(SecretProtectionError):
        replace(record.credential_snapshot(), ciphertext=encrypted.ciphertext, nonce=encrypted.nonce).decrypt(protector)
    record.clear_credential()
    assert record.ciphertext is record.nonce is record.encryption_key_id is None
    with pytest.raises(SecretProtectionError):
        record.credential_snapshot().decrypt(protector)


def test_oauth_setup_cannot_move_to_another_connection() -> None:
    protector = SecretProtector(key=b"k" * 32, encryption_key_id="test")
    record = MCPAuthorizationRecord(
        id="session-one",
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        connection_id="connection-one",
        credential_generation=0,
    )
    record.replace_credential("pkce-verifier", protector)
    record.connection_id = "connection-two"
    with pytest.raises(SecretProtectionError):
        record.credential_snapshot().decrypt(protector)


async def test_account_persists_only_resource_owned_material(connectivity_sessions, credential_protector):
    async with transaction(connectivity_sessions) as session:
        record = await session.get(AccountRecord, ACCOUNT_ID)
        encrypted = record.credential_snapshot()
        resource = record.to_resource()
        assert await session.scalar(select(func.count()).select_from(SecretRecord)) == 0
    assert encrypted.decrypt(credential_protector) == '{"token":"secret-value"}'
    assert "secret-value" not in resource.model_dump_json()
