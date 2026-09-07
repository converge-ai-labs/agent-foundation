from __future__ import annotations

from a13n_service.connectivity.connectors.models import (
    ConnectorConnectionRecord,
    ConnectorProviderRecord,
)
from a13n_service.connectivity.mcp.models import MCPConnectionRecord
from a13n_service.storage import transaction
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, ORG_ID, USER_ID, WORKSPACE_ID

CONNECTOR_ID = "cnr_1234567890abcdef"
CONNECTOR_CONNECTION_ID = "cconn_1234567890abcdef"
MCP_CONNECTION_ID = "mcpc_1234567890abcdef"
CONNECTOR_SECRET_ID = "sec_connector12345678"


async def seed_selection_sources(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with transaction(sessions) as session:
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
                ciphertext=b"encrypted",
                nonce=b"123456789012",
                encryption_key_id="test-key",
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
                name="Orders account",
                normalized_name="orders account",
                connector_key="orders",
                external_ref="external-account",
                external_user_correlation="usrh_workspace",
                safe_metadata_json={},
                status="ready",
                status_reason=None,
                version=1,
                setup_generation=1,
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
                name="Docs",
                normalized_name="docs",
                endpoint_url="https://mcp.example/rpc",
                auth_mode="none",
                static_header_names_json=[],
                status="ready",
                status_reason=None,
                version=1,
                credential_generation=0,
                refresh_claim_generation=0,
                refresh_claim_owner=None,
                refresh_claim_expires_at=None,
                deleted_at=None,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
