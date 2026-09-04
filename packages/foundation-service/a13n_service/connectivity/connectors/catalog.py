"""Bounded ConnectorProvider tool discovery and immutable catalog publication."""

from __future__ import annotations

from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial

from anyio import to_thread
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.connectors.contracts import ConnectorProviderError, ConnectorTool
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.domain import JsonObject
from a13n_service.connectivity.management import canonical_json
from a13n_service.ids import new_object_id
from a13n_service.secrets import InternalSecretError, InternalSecretService, SecretOperation
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .catalog_objects import ConnectorCatalogObjectStore
from .catalog_validation import MAX_CATALOG_BYTES, MAX_PAGES, MAX_TOOLS, validate_catalog_tools
from .connection_access import connection_binding, external_error
from .contracts import ConnectionBinding, ConnectorConnectionRuntime
from .errors import ConnectorError
from .management import (
    configure_provider,
    decode_credentials,
    require_connection,
    require_connector_provider,
    secret_context,
)
from .models import ConnectorConnectionRecord, ConnectorProviderRecord, ConnectorToolCatalogRecord

_JSON_OBJECT = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class CatalogSource:
    connection_id: str
    organization_id: str
    workspace_id: str
    connector: ConnectorProviderRecord
    binding: ConnectionBinding
    setup_generation: int
    catalog_generation: int
    credential_generation: int
    claim_generation: int
    claim_owner: str


class ConnectorCatalogService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: ConnectorProviderRegistry,
        secrets: InternalSecretService,
        objects: ConnectorCatalogObjectStore,
        *,
        instance_id: str = "connector-catalog",
        lease_seconds: int = 60,
        retention_seconds: int = 30 * 24 * 3600,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._adapters = adapters
        self._secrets = secrets
        self._objects = objects
        self._instance_id = instance_id
        self._lease_seconds = lease_seconds
        self._retention_seconds = retention_seconds
        self._clock = clock

    async def refresh(self, connection_id: str) -> str:
        source = await self._source(connection_id)
        try:
            raw = await self._secrets.resolve(
                secret_context(
                    source.connector,
                    operation=SecretOperation.reconciliation,
                    generation=source.credential_generation,
                )
            )
        except InternalSecretError as error:
            raise ConnectorError(
                "credential_unavailable", "ConnectorProvider credentials are unavailable.", status_code=503
            ) from error
        runtime = configure_provider(self._adapters, source.connector, decode_credentials(raw))
        try:
            async with aclosing(runtime), aclosing(runtime.connect(source.binding)) as connection_runtime:
                tools, provider_version = await _discover(connection_runtime)
        except ConnectorProviderError as error:
            raise external_error(error) from error
        compatibility_profile = runtime.compatibility_profile
        body = await to_thread.run_sync(
            partial(
                _catalog_bytes,
                connection_id=source.connection_id,
                compatibility_profile=compatibility_profile,
                provider_version=provider_version,
                connector_credential_generation=source.credential_generation,
                connection_setup_generation=source.setup_generation,
                tools=tools,
            )
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
            tool_count=len(tools),
            compatibility_profile=compatibility_profile,
            provider_version=provider_version,
        )
        if not published and object_created:
            await self._objects.delete(object_key)
        if not published:
            raise ConnectorError(
                "catalog_lost_race", "ConnectorProvider tool catalog changed concurrently.", status_code=409
            )
        return digest

    async def _source(self, connection_id: str) -> CatalogSource:
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            connector = await require_connector_provider(session, connection.connector_provider_id)
            if connection.status != "ready" or connection.external_ref is None or connector.status != "active":
                raise ConnectorError("connection_not_ready", "ConnectorConnection is not ready.", status_code=409)
            if (
                connection.catalog_claim_expires_at is not None
                and assume_utc(connection.catalog_claim_expires_at) > now
            ):
                raise ConnectorError(
                    "catalog_claimed", "ConnectorProvider catalog refresh is already running.", status_code=409
                )
            connection.catalog_claim_generation += 1
            connection.catalog_claim_owner = self._instance_id
            connection.catalog_claim_expires_at = now + timedelta(seconds=self._lease_seconds)
            return CatalogSource(
                connection_id=connection.id,
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                connector=connector,
                binding=await connection_binding(session, connection),
                setup_generation=connection.setup_generation,
                catalog_generation=connection.catalog_generation,
                credential_generation=connector.credential_generation,
                claim_generation=connection.catalog_claim_generation,
                claim_owner=self._instance_id,
            )

    async def _publish(
        self,
        source: CatalogSource,
        *,
        object_key: str,
        digest: str,
        size_bytes: int,
        tool_count: int,
        compatibility_profile: str,
        provider_version: str,
    ) -> bool:
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, source.connection_id, lock=True)
            connector = await require_connector_provider(session, connection.connector_provider_id)
            if not _source_matches(connection, connector, source, now=now):
                return False
            existing = await session.scalar(
                select(ConnectorToolCatalogRecord.id).where(
                    ConnectorToolCatalogRecord.connector_connection_id == source.connection_id,
                    ConnectorToolCatalogRecord.digest_sha256 == digest,
                )
            )
            if existing is None:
                session.add(
                    ConnectorToolCatalogRecord(
                        id=new_object_id("tcat"),
                        organization_id=source.organization_id,
                        workspace_id=source.workspace_id,
                        connector_connection_id=source.connection_id,
                        digest_sha256=digest,
                        object_key=object_key,
                        size_bytes=size_bytes,
                        tool_count=tool_count,
                        connector_credential_generation=source.credential_generation,
                        connection_setup_generation=source.setup_generation,
                        compatibility_profile=compatibility_profile,
                        provider_version=provider_version,
                        published_at=now,
                        retain_until=now + timedelta(seconds=self._retention_seconds),
                    )
                )
            connection.catalog_last_error_code = None
            connection.current_catalog_digest = digest
            connection.catalog_attempt_count = 0
            connection.catalog_available_at = now + timedelta(seconds=self._retention_seconds)
            connection.catalog_claim_owner = None
            connection.catalog_claim_expires_at = None
            return True


async def _discover(
    runtime: ConnectorConnectionRuntime,
) -> tuple[tuple[ConnectorTool, ...], str]:
    cursor: str | None = None
    tools: list[ConnectorTool] = []
    provider_version: str | None = None
    seen_cursors: set[str] = set()
    for _page_number in range(MAX_PAGES):
        page = await runtime.discover_tools(cursor=cursor)
        if provider_version is None:
            provider_version = page.provider_version
        elif page.provider_version != provider_version:
            raise ConnectorError(
                "catalog_incompatible", "ConnectorProvider catalog changed during discovery.", status_code=409
            )
        tools.extend(page.items)
        if len(tools) > MAX_TOOLS:
            raise ConnectorError(
                "catalog_too_large", "ConnectorProvider catalog exceeds its tool limit.", status_code=409
            )
        cursor = page.next_cursor
        if cursor is None:
            break
        if cursor in seen_cursors:
            raise ConnectorError(
                "catalog_incompatible", "ConnectorProvider catalog pagination is invalid.", status_code=409
            )
        seen_cursors.add(cursor)
    else:
        raise ConnectorError("catalog_too_large", "ConnectorProvider catalog exceeds its page limit.", status_code=409)
    if provider_version is None:
        raise ConnectorError("catalog_incompatible", "ConnectorProvider returned no catalog version.", status_code=409)
    return tuple(sorted(tools, key=lambda item: item.key)), provider_version


def _catalog_bytes(
    *,
    connection_id: str,
    compatibility_profile: str,
    provider_version: str,
    connector_credential_generation: int,
    connection_setup_generation: int,
    tools: tuple[ConnectorTool, ...],
) -> bytes:
    validate_catalog_tools(list(tools))
    value = {
        "schema_version": "1",
        "source_kind": "connector_connection",
        "connector_connection_id": connection_id,
        "compatibility_profile": compatibility_profile,
        "provider_version": provider_version,
        "connector_credential_generation": connector_credential_generation,
        "connection_setup_generation": connection_setup_generation,
        "tools": [tool.model_dump(mode="json") for tool in tools],
    }
    body = canonical_json(_JSON_OBJECT.validate_python(value)).encode()
    if not body or len(body) > MAX_CATALOG_BYTES:
        raise ConnectorError("catalog_too_large", "ConnectorProvider catalog exceeds its byte limit.", status_code=409)
    return body


def _source_matches(
    connection: ConnectorConnectionRecord,
    connector: ConnectorProviderRecord,
    source: CatalogSource,
    *,
    now: datetime,
) -> bool:
    return (
        connection.status == "ready"
        and connection.deleted_at is None
        and connection.catalog_claim_expires_at is not None
        and assume_utc(connection.catalog_claim_expires_at) > now
        and connection.external_ref == source.binding.external_ref
        and connection.connector_key == source.binding.connector_key
        and connection.setup_generation == source.setup_generation
        and connection.catalog_generation == source.catalog_generation
        and connection.catalog_claim_generation == source.claim_generation
        and connection.catalog_claim_owner == source.claim_owner
        and connector.status == "active"
        and connector.credential_generation == source.credential_generation
        and connector.type == source.connector.type
        and connector.configuration_json == source.connector.configuration_json
    )
