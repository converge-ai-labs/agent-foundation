from __future__ import annotations

import hashlib
from datetime import timedelta

from a13n_service.connectivity.connectors.models import (
    ConnectorConnectionRecord,
    ConnectorRecord,
    ConnectorToolCatalogRecord,
)
from a13n_service.connectivity.management import canonical_json
from a13n_service.connectivity.mcp.models import MCPConnectionRecord, MCPToolCatalogRecord
from a13n_service.secrets.models import SecretRecord
from a13n_service.storage import transaction
from a13n_service.storage.object_store import ObjectStore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import NOW, ORG_ID, USER_ID, WORKSPACE_ID

CONNECTOR_ID = "cnr_1234567890abcdef"
CONNECTOR_CONNECTION_ID = "cconn_1234567890abcdef"
MCP_CONNECTION_ID = "mcpc_1234567890abcdef"
CONNECTOR_SECRET_ID = "sec_connector12345678"
CONNECTOR_CATALOG_ID = "tcat_1234567890abcdef"
MCP_CATALOG_ID = "mcat_1234567890abcdef"


async def seed_selection_sources(
    sessions: async_sessionmaker[AsyncSession],
    objects: ObjectStore,
    *,
    connector_owner_id: str | None = None,
    mcp_owner_user_id: str | None = None,
) -> tuple[str, str]:
    connector_body = canonical_json(
        {
            "schema_version": "1",
            "source_kind": "connector_connection",
            "connector_connection_id": CONNECTOR_CONNECTION_ID,
            "compatibility_profile": "fake_connector@fake_v1",
            "provider_version": "2026-09",
            "connector_credential_generation": 1,
            "connection_setup_generation": 1,
            "tools": [
                {
                    "key": "create_order",
                    "description": "Create an order.",
                    "input_schema": {"type": "object", "properties": {}},
                    "output_schema": None,
                    "annotations": {},
                },
                {
                    "key": "find_order",
                    "description": "Find an order.",
                    "input_schema": {"type": "object", "properties": {}},
                    "output_schema": None,
                    "annotations": {},
                },
            ],
        }
    ).encode()
    connector_digest = hashlib.sha256(connector_body).hexdigest()
    connector_key = f"catalogs/{CONNECTOR_CONNECTION_ID}/{connector_digest}.json"
    await objects.put(connector_key, connector_body, content_type="application/json")

    mcp_body = canonical_json(
        {
            "schema_version": "1",
            "source_kind": "mcp_connection",
            "mcp_connection_id": MCP_CONNECTION_ID,
            "protocol_revision": "2025-11-25",
            "credential_generation": 0,
            "server": {"name": "docs", "version": "1.0"},
            "capabilities": {},
            "tools": [
                {
                    "name": "search_docs",
                    "description": "Search documentation.",
                    "input_schema": {"type": "object", "properties": {}},
                    "output_schema": None,
                    "annotations": {},
                }
            ],
        }
    ).encode()
    mcp_digest = hashlib.sha256(mcp_body).hexdigest()
    mcp_key = f"catalogs/{MCP_CONNECTION_ID}/{mcp_digest}.json"
    await objects.put(mcp_key, mcp_body, content_type="application/json")

    async with transaction(sessions) as session:
        session.add(
            SecretRecord(
                id=CONNECTOR_SECRET_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                owner_type="connector",
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
            ConnectorRecord(
                id=CONNECTOR_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                name="Orders",
                normalized_name="orders",
                driver_key="fake_connector",
                config_version="fake_v1",
                endpoint="https://connector.example",
                config_json={},
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
                connector_id=CONNECTOR_ID,
                owner_type="user" if connector_owner_id is not None else None,
                owner_id=connector_owner_id,
                name="Orders account",
                normalized_name="orders account",
                provider_key="orders",
                external_ref="external-account",
                safe_metadata_json={},
                status="ready",
                status_reason=None,
                version=1,
                setup_generation=1,
                revoke_generation=0,
                catalog_generation=1,
                current_catalog_digest=connector_digest,
                catalog_attempt_count=0,
                catalog_claim_generation=0,
                catalog_claim_owner=None,
                catalog_claim_expires_at=None,
                catalog_available_at=NOW,
                catalog_last_error_code=None,
                deleted_at=None,
                created_by_type="user",
                created_by_id=USER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(
            ConnectorToolCatalogRecord(
                id=CONNECTOR_CATALOG_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                connector_connection_id=CONNECTOR_CONNECTION_ID,
                digest_sha256=connector_digest,
                object_key=connector_key,
                size_bytes=len(connector_body),
                tool_count=2,
                connector_credential_generation=1,
                connection_setup_generation=1,
                compatibility_profile="fake_connector@fake_v1",
                provider_version="2026-09",
                published_at=NOW,
                retain_until=NOW + timedelta(days=30),
            )
        )
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
                catalog_generation=1,
                current_catalog_digest=mcp_digest,
                catalog_claim_generation=0,
                catalog_claim_owner=None,
                catalog_claim_expires_at=None,
                catalog_available_at=NOW,
                catalog_last_error_code=None,
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
        session.add(
            MCPToolCatalogRecord(
                id=MCP_CATALOG_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                mcp_connection_id=MCP_CONNECTION_ID,
                digest_sha256=mcp_digest,
                object_key=mcp_key,
                size_bytes=len(mcp_body),
                tool_count=1,
                credential_generation=0,
                catalog_generation=1,
                protocol_revision="2025-11-25",
                server_name="docs",
                server_version="1.0",
                published_at=NOW,
                retain_until=NOW + timedelta(days=30),
            )
        )
    return connector_digest, mcp_digest
