"""Fenced Remote MCP discovery and immutable catalog publication."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx2
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.ids import new_object_id
from a13n_service.secrets import (
    InternalSecretError,
    InternalSecretService,
    SecretOperation,
    SecretOwnerType,
    SecretUseContext,
)
from a13n_service.storage import transaction

from .catalog import catalog_bytes
from .catalog_objects import MCPCatalogObjectStore
from .credentials import MCPCredentialError, decode_request_headers
from .domain import MCP_PROTOCOL_REVISION
from .errors import MCPConnectionError
from .management import require_connection
from .models import MCPConnectionRecord, MCPToolCatalogRecord
from .protocol import MCPProtocolClient, MCPProtocolError


@dataclass(frozen=True, slots=True)
class CatalogSource:
    connection_id: str
    organization_id: str
    workspace_id: str
    endpoint_url: str
    auth_mode: str
    credential_generation: int
    catalog_generation: int
    claim_generation: int
    claim_owner: str


class MCPCatalogService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        protocol: MCPProtocolClient,
        secrets: InternalSecretService,
        objects: MCPCatalogObjectStore,
        *,
        instance_id: str,
        lease_seconds: int = 60,
        retention_seconds: int = 30 * 24 * 3600,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._protocol = protocol
        self._secrets = secrets
        self._objects = objects
        self._instance_id = instance_id
        self._lease_seconds = lease_seconds
        self._retention_seconds = retention_seconds
        self._clock = clock

    async def refresh(self, connection_id: str) -> str:
        source = await self._claim(connection_id)
        try:
            headers = await self._headers(source)
            discovery = await self._protocol.discover(source.endpoint_url, credential_headers=headers)
            body = await catalog_bytes(
                connection_id=source.connection_id,
                credential_generation=source.credential_generation,
                discovery=discovery,
            )
            object_key, digest, object_created = await self._objects.retain(
                organization_id=source.organization_id,
                workspace_id=source.workspace_id,
                connection_id=source.connection_id,
                body=body,
            )
            published = await self._publish(
                source,
                object_key=object_key,
                digest=digest,
                size_bytes=len(body),
                tool_count=len(discovery.tools),
                server_name=discovery.server_name,
                server_version=discovery.server_version,
            )
            if not published and object_created:
                await self._objects.delete(object_key)
            if not published:
                raise MCPConnectionError(
                    "catalog_lost_race",
                    "MCPConnection changed during discovery.",
                    status_code=409,
                )
            return digest
        except (
            MCPProtocolError,
            InternalSecretError,
            MCPCredentialError,
            MCPConnectionError,
            httpx2.HTTPError,
        ) as error:
            await self._record_failure(source, error)
            if isinstance(error, MCPConnectionError):
                raise
            raise MCPConnectionError(
                "mcp_discovery_failed",
                "Remote MCP discovery failed.",
                status_code=409 if isinstance(error, MCPProtocolError) else 503,
            ) from error

    async def _claim(self, connection_id: str) -> CatalogSource:
        now = self._clock()
        async with transaction(self._sessions) as session:
            record = await require_connection(session, connection_id, lock=True)
            if record.status == "disabled":
                raise MCPConnectionError("connection_disabled", "MCPConnection is disabled.", status_code=409)
            if record.auth_mode != "none" and record.credential_secret_id is None:
                raise MCPConnectionError(
                    "credentials_required",
                    "MCPConnection credentials are required.",
                    status_code=409,
                )
            if record.catalog_claim_expires_at is not None and _utc(record.catalog_claim_expires_at) > now:
                raise MCPConnectionError("catalog_claimed", "MCP discovery is already running.", status_code=409)
            record.catalog_generation += 1
            record.catalog_claim_generation += 1
            record.catalog_claim_owner = self._instance_id
            record.catalog_claim_expires_at = now + timedelta(seconds=self._lease_seconds)
            return CatalogSource(
                connection_id=record.id,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                endpoint_url=record.endpoint_url,
                auth_mode=record.auth_mode,
                credential_generation=record.credential_generation,
                catalog_generation=record.catalog_generation,
                claim_generation=record.catalog_claim_generation,
                claim_owner=self._instance_id,
            )

    async def _headers(self, source: CatalogSource) -> dict[str, str]:
        if source.auth_mode == "none":
            return {}
        raw = await self._secrets.resolve(secret_context_from_source(source, operation=SecretOperation.reconciliation))
        return decode_request_headers(raw)

    async def _publish(
        self,
        source: CatalogSource,
        *,
        object_key: str,
        digest: str,
        size_bytes: int,
        tool_count: int,
        server_name: str,
        server_version: str,
    ) -> bool:
        now = self._clock()
        async with transaction(self._sessions) as session:
            record = await require_connection(session, source.connection_id, lock=True)
            if not _matches(record, source):
                return False
            existing = await session.scalar(
                select(MCPToolCatalogRecord.id).where(
                    MCPToolCatalogRecord.mcp_connection_id == source.connection_id,
                    MCPToolCatalogRecord.digest_sha256 == digest,
                )
            )
            if existing is None:
                session.add(
                    MCPToolCatalogRecord(
                        id=new_object_id("mcat"),
                        organization_id=source.organization_id,
                        workspace_id=source.workspace_id,
                        mcp_connection_id=source.connection_id,
                        digest_sha256=digest,
                        object_key=object_key,
                        size_bytes=size_bytes,
                        tool_count=tool_count,
                        credential_generation=source.credential_generation,
                        catalog_generation=source.catalog_generation,
                        protocol_revision=MCP_PROTOCOL_REVISION,
                        server_name=server_name[:128],
                        server_version=server_version[:128],
                        published_at=now,
                        retain_until=now + timedelta(seconds=self._retention_seconds),
                    )
                )
            record.status = "ready"
            record.status_reason = None
            record.current_catalog_digest = digest
            record.catalog_last_error_code = None
            record.catalog_available_at = now + timedelta(seconds=self._retention_seconds)
            record.catalog_claim_owner = None
            record.catalog_claim_expires_at = None
            record.updated_at = now
            return True

    async def _record_failure(self, source: CatalogSource, error: Exception) -> None:
        if isinstance(error, (MCPProtocolError, MCPConnectionError)):
            code = error.code
        elif isinstance(error, (InternalSecretError, MCPCredentialError)):
            code = "credential_unavailable"
        else:
            code = "mcp_unavailable"
        async with transaction(self._sessions) as session:
            record = await require_connection(session, source.connection_id, lock=True)
            if not _matches(record, source):
                return
            record.catalog_last_error_code = code[:128]
            record.catalog_available_at = self._clock() + timedelta(seconds=60)
            record.catalog_claim_owner = None
            record.catalog_claim_expires_at = None
            reason = _action_required_reason(source.auth_mode, code)
            if reason is not None:
                record.status = "action_required"
                record.status_reason = reason
                record.updated_at = self._clock()


def secret_context_from_source(source: CatalogSource, *, operation: SecretOperation) -> SecretUseContext:
    return SecretUseContext(
        organization_id=source.organization_id,
        workspace_id=source.workspace_id,
        owner_type=SecretOwnerType.mcp_connection,
        owner_id=source.connection_id,
        key="credential_bundle",
        operation=operation,
        credential_generation=source.credential_generation,
    )


def _matches(record: MCPConnectionRecord, source: CatalogSource) -> bool:
    return (
        record.deleted_at is None
        and record.status != "disabled"
        and record.endpoint_url == source.endpoint_url
        and record.auth_mode == source.auth_mode
        and record.credential_generation == source.credential_generation
        and record.catalog_generation == source.catalog_generation
        and record.catalog_claim_generation == source.claim_generation
        and record.catalog_claim_owner == source.claim_owner
    )


def _action_required_reason(auth_mode: str, code: str) -> str | None:
    if code == "authorization_required":
        return "incompatible" if auth_mode == "none" else "reauthorization_required"
    if code in {
        "catalog_too_deep",
        "catalog_too_large",
        "duplicate_tool_name",
        "incompatible_notification",
        "incompatible_protocol",
        "invalid_catalog_cursor",
        "invalid_jsonrpc_response",
        "invalid_server_capabilities",
        "invalid_server_info",
        "invalid_tool_catalog",
        "invalid_tool_schema",
        "origin_change_redirect",
        "unsafe_endpoint",
        "unsupported_content_type",
    }:
        return "incompatible"
    return None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
