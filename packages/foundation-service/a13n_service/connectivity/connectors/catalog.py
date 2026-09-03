"""Bounded Connector tool discovery and immutable catalog publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial

from anyio import to_thread
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.connectors.adapters import ConnectorAdapter, ConnectorAdapterError, ConnectorTool
from a13n_service.connectivity.ingress.domain import JsonObject
from a13n_service.connectivity.management import canonical_json
from a13n_service.ids import new_object_id
from a13n_service.secrets import InternalSecretError, InternalSecretService, SecretOperation
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .catalog_objects import ConnectorCatalogObjectStore
from .connection_access import external_error
from .errors import ConnectorError
from .management import decode_credentials, require_adapter, require_connection, require_connector, secret_context
from .models import ConnectorConnectionRecord, ConnectorRecord, ConnectorToolCatalogRecord

MAX_PAGES = 128
MAX_TOOLS = 2_048
MAX_CATALOG_BYTES = 16 * 1024 * 1024
MAX_TOOL_NAME_BYTES = 128
MAX_DESCRIPTION_BYTES = 16 * 1024
MAX_SCHEMAS_BYTES = 256 * 1024
MAX_JSON_DEPTH = 64

_JSON_OBJECT = TypeAdapter(JsonObject)


@dataclass(frozen=True, slots=True)
class CatalogSource:
    connection_id: str
    organization_id: str
    workspace_id: str
    connector: ConnectorRecord
    external_ref: str
    provider_key: str
    setup_generation: int
    catalog_generation: int
    credential_generation: int
    claim_generation: int
    claim_owner: str


class ConnectorCatalogService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        adapters: AdapterRegistry[ConnectorAdapter],
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
                "credential_unavailable", "Connector credentials are unavailable.", status_code=503
            ) from error
        adapter = require_adapter(
            self._adapters,
            source.connector.driver_key,
            source.connector.config_version,
        )
        try:
            tools, provider_version = await _discover(adapter, source, decode_credentials(raw))
        except ConnectorAdapterError as error:
            raise external_error(error) from error
        compatibility_profile = f"{source.connector.driver_key}@{source.connector.config_version}"
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
            raise ConnectorError("catalog_lost_race", "Connector tool catalog changed concurrently.", status_code=409)
        return digest

    async def _source(self, connection_id: str) -> CatalogSource:
        now = self._clock()
        async with transaction(self._sessions) as session:
            connection = await require_connection(session, connection_id, lock=True)
            connector = await require_connector(session, connection.connector_id)
            if connection.status != "ready" or connection.external_ref is None or connector.status != "active":
                raise ConnectorError("connection_not_ready", "ConnectorConnection is not ready.", status_code=409)
            if (
                connection.catalog_claim_expires_at is not None
                and assume_utc(connection.catalog_claim_expires_at) > now
            ):
                raise ConnectorError(
                    "catalog_claimed", "Connector catalog refresh is already running.", status_code=409
                )
            connection.catalog_claim_generation += 1
            connection.catalog_claim_owner = self._instance_id
            connection.catalog_claim_expires_at = now + timedelta(seconds=self._lease_seconds)
            return CatalogSource(
                connection_id=connection.id,
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                connector=connector,
                external_ref=connection.external_ref,
                provider_key=connection.provider_key,
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
            connector = await require_connector(session, connection.connector_id)
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
    adapter: ConnectorAdapter,
    source: CatalogSource,
    credentials: JsonObject,
) -> tuple[tuple[ConnectorTool, ...], str]:
    cursor: str | None = None
    tools: list[ConnectorTool] = []
    provider_version: str | None = None
    seen_cursors: set[str] = set()
    for _page_number in range(MAX_PAGES):
        page = await adapter.list_tools(
            endpoint=source.connector.endpoint,
            connector_config=source.connector.config_json,
            credentials=credentials,
            external_ref=source.external_ref,
            provider_key=source.provider_key,
            cursor=cursor,
        )
        if provider_version is None:
            provider_version = page.provider_version
        elif page.provider_version != provider_version:
            raise ConnectorError("catalog_incompatible", "Connector catalog changed during discovery.", status_code=409)
        tools.extend(page.items)
        if len(tools) > MAX_TOOLS:
            raise ConnectorError("catalog_too_large", "Connector catalog exceeds its tool limit.", status_code=409)
        cursor = page.next_cursor
        if cursor is None:
            break
        if cursor in seen_cursors:
            raise ConnectorError("catalog_incompatible", "Connector catalog pagination is invalid.", status_code=409)
        seen_cursors.add(cursor)
    else:
        raise ConnectorError("catalog_too_large", "Connector catalog exceeds its page limit.", status_code=409)
    if provider_version is None:
        raise ConnectorError("catalog_incompatible", "Connector returned no catalog version.", status_code=409)
    return tuple(sorted(tools, key=lambda item: item.key)), provider_version


def validate_catalog_tools(tools: list[ConnectorTool]) -> None:
    seen: set[str] = set()
    for tool in tools:
        if tool.key in seen:
            raise ConnectorError("catalog_incompatible", "Connector catalog contains duplicate tools.", status_code=409)
        seen.add(tool.key)
        if len(tool.key.encode()) > MAX_TOOL_NAME_BYTES or len(tool.description.encode()) > MAX_DESCRIPTION_BYTES:
            raise ConnectorError("catalog_too_large", "Connector tool metadata exceeds its limit.", status_code=409)
        schemas = canonical_json({"input": tool.input_schema, "output": tool.output_schema}).encode()
        if len(schemas) > MAX_SCHEMAS_BYTES:
            raise ConnectorError("catalog_too_large", "Connector tool schema exceeds its limit.", status_code=409)
        _require_depth(tool.input_schema)
        _check_schema(tool.input_schema)
        if tool.output_schema is not None:
            _require_depth(tool.output_schema)
            _check_schema(tool.output_schema)
        _require_depth(tool.annotations)


def _require_depth(value: object) -> None:
    pending = [(value, 1)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise ConnectorError("catalog_too_deep", "Connector catalog exceeds its nesting limit.", status_code=409)
        if isinstance(current, dict):
            pending.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            pending.extend((item, depth + 1) for item in current)


def _check_schema(value: JsonObject) -> None:
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as error:
        raise ConnectorError(
            "catalog_incompatible", "Connector returned an invalid JSON Schema.", status_code=409
        ) from error


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
        raise ConnectorError("catalog_too_large", "Connector catalog exceeds its byte limit.", status_code=409)
    return body


def _source_matches(
    connection: ConnectorConnectionRecord,
    connector: ConnectorRecord,
    source: CatalogSource,
    *,
    now: datetime,
) -> bool:
    return (
        connection.status == "ready"
        and connection.deleted_at is None
        and connection.catalog_claim_expires_at is not None
        and assume_utc(connection.catalog_claim_expires_at) > now
        and connection.external_ref == source.external_ref
        and connection.provider_key == source.provider_key
        and connection.setup_generation == source.setup_generation
        and connection.catalog_generation == source.catalog_generation
        and connection.catalog_claim_generation == source.claim_generation
        and connection.catalog_claim_owner == source.claim_owner
        and connector.status == "active"
        and connector.credential_generation == source.credential_generation
        and connector.driver_key == source.connector.driver_key
        and connector.config_version == source.connector.config_version
        and connector.endpoint == source.connector.endpoint
    )
