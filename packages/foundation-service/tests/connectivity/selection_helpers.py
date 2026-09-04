from __future__ import annotations

from a13n_service.connectivity.connectors.models import (
    ConnectorConnectionRecord,
    ConnectorProviderRecord,
)
from a13n_service.connectivity.mcp.models import MCPConnectionRecord
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, ORG_ID, USER_ID, WORKSPACE_ID

CONNECTOR_ID = "cnr_1234567890abcdef"
CONNECTOR_CONNECTION_ID = "cconn_1234567890abcdef"
MCP_CONNECTION_ID = "mcpc_1234567890abcdef"
CONNECTOR_SECRET_ID = "sec_connector12345678"


async def seed_selection_sources(
    sessions: async_sessionmaker[AsyncSession],
    *,
    connector_owner_id: str | None = None,
    mcp_owner_user_id: str | None = None,
) -> None:
    async with transaction(sessions) as session:
        session.add(
            SecretRecord(
                id=CONNECTOR_SECRET_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                owner_type="connector_provider",
                owner_id=CONNECTOR_ID,
                key="credential_bundle",
                version=1,
                ciphertext=b"encrypted",
                nonce=b"123456789012",
                encryption_key_id="test-key",
                created_at=NOW,
                value_updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.flush()
        session.add(
            ConnectorProviderRecord(
                id=CONNECTOR_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                name="Orders",
                normalized_name="orders",
                type="fake_connector",
                configuration_json={"endpoint": "https://connector.example"},
                status="active",
                version=1,
                credential_secret_id=CONNECTOR_SECRET_ID,
                credential_generation=1,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(
            ConnectorConnectionRecord(
                id=CONNECTOR_CONNECTION_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                connector_provider_id=CONNECTOR_ID,
                owner_type="user" if connector_owner_id is not None else None,
                owner_id=connector_owner_id,
                name="Orders account",
                normalized_name="orders account",
                connector_key="orders",
                external_ref="external-account",
                safe_metadata_json={},
                status="ready",
                status_reason=None,
                version=1,
                setup_generation=1,
                revoke_generation=0,
                deleted_at=None,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()

        session.add(
            MCPConnectionRecord(
                id=MCP_CONNECTION_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                owner_user_id=mcp_owner_user_id,
                name="Docs",
                normalized_name="docs",
                endpoint_url="https://mcp.example/rpc",
                auth_mode="none",
                static_header_names_json=[],
                status="ready",
                status_reason=None,
                version=1,
                credential_secret_id=None,
                credential_generation=0,
                refresh_claim_generation=0,
                refresh_claim_owner=None,
                refresh_claim_expires_at=None,
                refresh_available_at=NOW,
                refresh_last_error_code=None,
                cleanup_pending=False,
                cleanup_attempt_count=0,
                cleanup_available_at=NOW,
                cleanup_last_error_code=None,
                deleted_at=None,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
