"""Bounded cleanup for expired Connectivity evidence and immutable objects."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import anyio
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.interactions.models import RunRecord
from a13n_service.storage import ObjectConflict, ObjectStore, ObjectStoreError, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .connectors.models import ConnectorConnectionRecord, ConnectorToolCatalogRecord
from .mcp.models import MCPConnectionRecord, MCPToolCatalogRecord

logger = logging.getLogger("a13n_service.connectivity.retention")
CatalogKind = Literal["connector_provider", "mcp"]

_CONNECTOR_CATALOG_KEY = re.compile(
    r"^tenants/[^/]+/workspaces/[^/]+/connectivity/catalogs/version-1/([^/]+)/([0-9a-f]{64})\.json$"
)
_MCP_CATALOG_KEY = re.compile(
    r"^tenants/[^/]+/workspaces/[^/]+/connectivity/mcp-catalogs/version-1/([^/]+)/([0-9a-f]{64})\.json$"
)
_SNAPSHOT_KEY = re.compile(r"^tenants/[^/]+/workspaces/[^/]+/runs/([^/]+)/mcp-tool-snapshots/([0-9a-f]{64})\.json$")


@dataclass(frozen=True, slots=True)
class _SourceClaim:
    kind: CatalogKind
    source_id: str
    generation: int


class CatalogRetentionReconciler:
    """Delete only expired, unreferenced tool catalogs and Run snapshots."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        objects: ObjectStore,
        *,
        instance_id: str,
        poll_interval_seconds: float = 60,
        lease_seconds: float = 60,
        object_grace_seconds: float = 3600,
        batch_size: int = 25,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._instance_id = instance_id
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._object_grace_seconds = object_grace_seconds
        self._batch_size = batch_size
        self._clock = clock
        self._object_cursor: str | None = None

    async def run(self) -> None:
        while True:
            try:
                await self.reconcile_once()
            except anyio.get_cancelled_exc_class():
                raise
            except Exception:
                logger.exception(
                    "connectivity_retention_reconcile_failed",
                    extra={"event": "connectivity_retention_reconcile_failed"},
                )
            await anyio.sleep(self._poll_interval_seconds)

    async def reconcile_once(self) -> int:
        cleaned = await self._clean_catalog("connector_provider")
        cleaned += await self._clean_catalog("mcp")
        cleaned += await self._clean_orphan_objects()
        return cleaned

    async def _clean_catalog(self, kind: CatalogKind) -> int:
        now = self._clock()
        catalog_model = ConnectorToolCatalogRecord if kind == "connector_provider" else MCPToolCatalogRecord
        source_model = ConnectorConnectionRecord if kind == "connector_provider" else MCPConnectionRecord
        source_id_column = (
            ConnectorToolCatalogRecord.connector_connection_id
            if kind == "connector_provider"
            else MCPToolCatalogRecord.mcp_connection_id
        )
        claim: _SourceClaim | None = None
        object_key: str | None = None
        async with transaction(self._sessions) as session:
            catalogs = tuple(
                (
                    await session.scalars(
                        select(catalog_model)
                        .join(source_model, source_model.id == source_id_column)
                        .where(
                            catalog_model.retain_until <= now,
                            or_(
                                source_model.current_catalog_digest.is_(None),
                                source_model.current_catalog_digest != catalog_model.digest_sha256,
                            ),
                        )
                        .order_by(catalog_model.retain_until, catalog_model.id)
                        .limit(self._batch_size)
                        .with_for_update(of=catalog_model, skip_locked=True)
                    )
                ).all()
            )
            for catalog in catalogs:
                source_id = getattr(catalog, source_id_column.key)
                if await self._catalog_is_referenced(
                    session,
                    kind=kind,
                    source_id=source_id,
                    digest=catalog.digest_sha256,
                ):
                    continue
                candidate_claim, source_exists = await self._claim_source(session, kind, source_id, now=now)
                if source_exists and candidate_claim is None:
                    continue
                claim = candidate_claim
                object_key = catalog.object_key
                await session.delete(catalog)
                break
        if object_key is None:
            return 0
        await self._delete_object(object_key)
        if claim is not None:
            await self._release_claim(claim)
        return 1

    async def _clean_orphan_objects(self) -> int:
        cutoff = self._clock() - timedelta(seconds=self._object_grace_seconds)
        try:
            page = await self._objects.list(
                prefix="tenants/",
                cursor=self._object_cursor,
                limit=self._batch_size,
            )
        except ObjectStoreError:
            return 0
        self._object_cursor = page.cursor
        cleaned = 0
        for item in page.items:
            if assume_utc(item.modified_at) > cutoff:
                continue
            match = _SNAPSHOT_KEY.fullmatch(item.key)
            if match is not None:
                if not await self._run_snapshot_exists(*match.groups()):
                    cleaned += await self._delete_object(item.key, version=item.version)
                continue
            kind, match = _catalog_match(item.key)
            if match is None:
                continue
            source_id, digest = match.groups()
            if await self._catalog_row_exists(kind, source_id, digest):
                continue
            claim, source_exists = await self._claim_source_in_new_transaction(kind, source_id)
            if source_exists and claim is None:
                continue
            try:
                if not await self._catalog_is_current(kind, source_id, digest) and not await self._catalog_row_exists(
                    kind, source_id, digest
                ):
                    cleaned += await self._delete_object(item.key, version=item.version)
            finally:
                if claim is not None:
                    await self._release_claim(claim)
        return cleaned

    async def _catalog_is_referenced(
        self,
        session: AsyncSession,
        *,
        kind: CatalogKind,
        source_id: str,
        digest: str,
    ) -> bool:
        source_key = "connector_connection_id" if kind == "connector_provider" else "mcp_connection_id"
        revision_column = "connector_tools" if kind == "connector_provider" else "mcp_tools"
        run_column = (
            "connector_connection_selections_json" if kind == "connector_provider" else "mcp_connection_selections_json"
        )
        dialect = session.bind.dialect.name if session.bind is not None else ""
        if dialect == "postgresql":
            query = text(
                f"""SELECT EXISTS (
                    SELECT 1 FROM agent_revisions AS revision,
                    jsonb_array_elements(revision.{revision_column}::jsonb) AS selection
                    WHERE selection ->> :source_key = :source_id
                ) OR EXISTS (
                    SELECT 1 FROM runs AS run,
                    jsonb_array_elements(run.{run_column}::jsonb) AS selection
                    WHERE selection ->> :source_key = :source_id
                      AND selection ->> 'tool_catalog_digest' = :digest
                )"""
            )
        else:
            query = text(
                f"""SELECT EXISTS (
                    SELECT 1 FROM agent_revisions AS revision,
                    json_each(revision.{revision_column}) AS selection
                    WHERE json_extract(selection.value, '$.' || :source_key) = :source_id
                ) OR EXISTS (
                    SELECT 1 FROM runs AS run,
                    json_each(run.{run_column}) AS selection
                    WHERE json_extract(selection.value, '$.' || :source_key) = :source_id
                      AND json_extract(selection.value, '$.tool_catalog_digest') = :digest
                )"""
            )
        return bool(
            await session.scalar(
                query,
                {"source_key": source_key, "source_id": source_id, "digest": digest},
            )
        )

    async def _claim_source_in_new_transaction(
        self, kind: CatalogKind, source_id: str
    ) -> tuple[_SourceClaim | None, bool]:
        async with transaction(self._sessions) as session:
            return await self._claim_source(session, kind, source_id, now=self._clock())

    async def _claim_source(
        self,
        session: AsyncSession,
        kind: CatalogKind,
        source_id: str,
        *,
        now: datetime,
    ) -> tuple[_SourceClaim | None, bool]:
        model = ConnectorConnectionRecord if kind == "connector_provider" else MCPConnectionRecord
        source = await session.scalar(select(model).where(model.id == source_id).with_for_update())
        if source is None:
            return None, False
        if source.catalog_claim_expires_at is not None and assume_utc(source.catalog_claim_expires_at) > now:
            return None, True
        source.catalog_claim_generation += 1
        source.catalog_claim_owner = self._instance_id
        source.catalog_claim_expires_at = now + timedelta(seconds=self._lease_seconds)
        return _SourceClaim(kind, source_id, source.catalog_claim_generation), True

    async def _release_claim(self, claim: _SourceClaim) -> None:
        async with transaction(self._sessions) as session:
            await self._release_claim_in_session(session, claim)

    async def _release_claim_in_session(self, session: AsyncSession, claim: _SourceClaim) -> None:
        model = ConnectorConnectionRecord if claim.kind == "connector_provider" else MCPConnectionRecord
        source = await session.scalar(select(model).where(model.id == claim.source_id).with_for_update())
        if (
            source is not None
            and source.catalog_claim_owner == self._instance_id
            and source.catalog_claim_generation == claim.generation
        ):
            source.catalog_claim_owner = None
            source.catalog_claim_expires_at = None

    async def _catalog_row_exists(self, kind: CatalogKind, source_id: str, digest: str) -> bool:
        model = ConnectorToolCatalogRecord if kind == "connector_provider" else MCPToolCatalogRecord
        source_column = (
            ConnectorToolCatalogRecord.connector_connection_id
            if kind == "connector_provider"
            else MCPToolCatalogRecord.mcp_connection_id
        )
        async with transaction(self._sessions) as session:
            return (
                await session.scalar(select(model.id).where(source_column == source_id, model.digest_sha256 == digest))
            ) is not None

    async def _catalog_is_current(self, kind: CatalogKind, source_id: str, digest: str) -> bool:
        model = ConnectorConnectionRecord if kind == "connector_provider" else MCPConnectionRecord
        async with transaction(self._sessions) as session:
            return (
                await session.scalar(
                    select(model.id).where(
                        model.id == source_id,
                        model.current_catalog_digest == digest,
                    )
                )
            ) is not None

    async def _run_snapshot_exists(self, run_id: str, digest: str) -> bool:
        async with transaction(self._sessions) as session:
            return (
                await session.scalar(
                    select(RunRecord.id).where(
                        RunRecord.id == run_id,
                        RunRecord.mcp_tool_snapshot_digest_sha256 == digest,
                    )
                )
            ) is not None

    async def _delete_object(self, key: str, *, version: str | None = None) -> int:
        try:
            await self._objects.delete(key, if_match=version)
        except (ObjectConflict, ObjectStoreError):
            return 0
        return 1


def _catalog_match(key: str) -> tuple[CatalogKind, re.Match[str] | None]:
    connector = _CONNECTOR_CATALOG_KEY.fullmatch(key)
    if connector is not None:
        return "connector_provider", connector
    return "mcp", _MCP_CATALOG_KEY.fullmatch(key)
