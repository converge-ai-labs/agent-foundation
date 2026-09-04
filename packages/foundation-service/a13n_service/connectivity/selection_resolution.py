"""Two-phase ConnectorConnection and MCPConnection selection resolution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from anyio import CapacityLimiter, create_task_group
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.connectors.models import (
    ConnectorConnectionRecord,
    ConnectorRecord,
    ConnectorToolCatalogRecord,
)
from a13n_service.connectivity.mcp.models import MCPConnectionRecord, MCPToolCatalogRecord
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_workspace
from a13n_service.storage import ObjectStore, short_session

from .selection_domain import ConnectorConnectionRunSelection, MCPConnectionRunSelection, SourceKind
from .tool_snapshots import (
    CatalogObject,
    CatalogTool,
    MCPToolSnapshotStore,
    SelectedCatalog,
    StoredToolSnapshot,
    ToolSnapshotError,
    build_snapshot,
    read_catalog,
)

if TYPE_CHECKING:
    from a13n_service.agents.domain import ConnectorConnectionToolSelection, MCPConnectionToolSelection

_CATALOG_READ_CONCURRENCY = 16


class ConnectivitySelectionError(RuntimeError):
    def __init__(self, code: str, *, path: str) -> None:
        super().__init__(code)
        self.code = code
        self.path = path


@dataclass(frozen=True, slots=True)
class _SourceIdentity:
    source_kind: SourceKind
    alias: str
    source_id: str
    connector_id: str | None
    exposure: Literal["direct", "catalog"]
    catalog_digest: str
    catalog_object_key: str
    catalog_size_bytes: int
    credential_generation: int
    source_generation: int
    compatibility_profile: str


@dataclass(frozen=True, slots=True)
class _PreparedSource(_SourceIdentity):
    requested_tool_keys: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class _SourceEvidence(_SourceIdentity):
    allowed_tool_keys: tuple[str, ...]
    tools: tuple[CatalogTool, ...]


@dataclass(frozen=True, slots=True)
class PreparedRevisionConnectivity:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    sources: tuple[_SourceEvidence, ...]


@dataclass(frozen=True, slots=True)
class FrozenRunConnectivity:
    connector_connection_selections: tuple[ConnectorConnectionRunSelection, ...]
    mcp_connection_selections: tuple[MCPConnectionRunSelection, ...]
    mcp_tool_snapshot: StoredToolSnapshot


@dataclass(frozen=True, slots=True)
class PreparedRunConnectivity(PreparedRevisionConnectivity):
    run_id: str
    frozen: FrozenRunConnectivity


class ConnectivitySelectionResolver:
    """Resolve local catalogs without introducing any external execution path."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], objects: ObjectStore) -> None:
        self._sessions = sessions
        self._objects = objects
        self._snapshots = MCPToolSnapshotStore(objects)

    async def prepare_revision_creation(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        connector_tools: Mapping[str, ConnectorConnectionToolSelection],
        mcp_tools: Mapping[str, MCPConnectionToolSelection],
    ) -> PreparedRevisionConnectivity:
        sources = await self._prepare_sources(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_tools=connector_tools,
            mcp_tools=mcp_tools,
        )
        return PreparedRevisionConnectivity(actor, organization_id, workspace_id, sources)

    async def freeze_revision_creation(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedRevisionConnectivity,
    ) -> None:
        await self._freeze_sources(session, prepared)

    async def prepare_invocation(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        run_id: str,
        connector_tools: Mapping[str, ConnectorConnectionToolSelection],
        mcp_tools: Mapping[str, MCPConnectionToolSelection],
    ) -> PreparedRunConnectivity:
        sources = await self._prepare_sources(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_tools=connector_tools,
            mcp_tools=mcp_tools,
        )
        connector_sources = tuple(source for source in sources if source.source_kind == "connector_connection")
        mcp_sources = tuple(source for source in sources if source.source_kind == "mcp_connection")
        connector_selections = tuple(
            ConnectorConnectionRunSelection(
                connector_connection_id=source.source_id,
                connector_id=_required_connector_id(source),
                exposure=source.exposure,
                allowed_tool_keys=source.allowed_tool_keys,
                tool_catalog_digest=source.catalog_digest,
            )
            for source in connector_sources
        )
        mcp_selections = tuple(
            MCPConnectionRunSelection(
                mcp_connection_id=source.source_id,
                exposure=source.exposure,
                allowed_tool_keys=source.allowed_tool_keys,
                tool_catalog_digest=source.catalog_digest,
            )
            for source in mcp_sources
        )
        selected_catalogs = tuple(
            SelectedCatalog(
                source_kind=source.source_kind,
                source_alias=source.alias,
                selection_index=index,
                exposure=source.exposure,
                tools=source.tools,
            )
            for group in (connector_sources, mcp_sources)
            for index, source in enumerate(group)
        )
        try:
            snapshot, body = await build_snapshot(run_id, selected_catalogs)
            stored = await self._snapshots.retain(
                organization_id=organization_id,
                workspace_id=workspace_id,
                run_id=run_id,
                snapshot=snapshot,
                body=body,
            )
        except ToolSnapshotError as error:
            raise ConnectivitySelectionError(error.code, path="connectivity") from error
        return PreparedRunConnectivity(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            sources=sources,
            run_id=run_id,
            frozen=FrozenRunConnectivity(connector_selections, mcp_selections, stored),
        )

    async def freeze_invocation(
        self,
        session: AsyncSession,
        *,
        prepared: PreparedRunConnectivity,
    ) -> FrozenRunConnectivity:
        await self._freeze_sources(session, prepared)
        return prepared.frozen

    async def _prepare_sources(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        connector_tools: Mapping[str, ConnectorConnectionToolSelection],
        mcp_tools: Mapping[str, MCPConnectionToolSelection],
    ) -> tuple[_SourceEvidence, ...]:
        _reject_duplicate_sources(connector_tools, mcp_tools)
        async with short_session(self._sessions) as session:
            connector_sources = await self._connector_sources(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                selections=connector_tools,
            )
            mcp_sources = await self._mcp_sources(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                selections=mcp_tools,
            )
        sources = connector_sources + mcp_sources
        resolved: dict[tuple[SourceKind, str], _SourceEvidence | ConnectivitySelectionError] = {}
        limiter = CapacityLimiter(_CATALOG_READ_CONCURRENCY)

        async def load(source: _PreparedSource) -> None:
            try:
                async with limiter:
                    catalog_tools = await read_catalog(
                        self._objects,
                        CatalogObject(
                            source_kind=source.source_kind,
                            source_id=source.source_id,
                            object_key=source.catalog_object_key,
                            digest_sha256=source.catalog_digest,
                            size_bytes=source.catalog_size_bytes,
                            credential_generation=source.credential_generation,
                            source_generation=source.source_generation,
                            compatibility_profile=source.compatibility_profile,
                        ),
                    )
            except ToolSnapshotError as error:
                item: _SourceEvidence | ConnectivitySelectionError = ConnectivitySelectionError(
                    error.code,
                    path=_source_path(source),
                )
            else:
                try:
                    selected_tools = _select_tools(
                        catalog_tools,
                        source.requested_tool_keys,
                        path=_source_path(source),
                    )
                except ConnectivitySelectionError as error:
                    item = error
                else:
                    item = _replace_tools(source, selected_tools)
            resolved[(source.source_kind, source.alias)] = item

        async with create_task_group() as tasks:
            for source in sources:
                tasks.start_soon(load, source)
        result: list[_SourceEvidence] = []
        for source in sources:
            item = resolved[(source.source_kind, source.alias)]
            if isinstance(item, ConnectivitySelectionError):
                raise item
            result.append(item)
        if sum(len(source.tools) for source in result) > 2_048:
            raise ConnectivitySelectionError("tool_selection_too_large", path="connectivity")
        return tuple(result)

    async def _connector_sources(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        selections: Mapping[str, ConnectorConnectionToolSelection],
    ) -> tuple[_PreparedSource, ...]:
        if not selections:
            return ()
        await _authorize_source_kind(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.connector_connection_read,
            path="connector_tools",
        )
        source_ids = tuple(selection.connector_connection_id for selection in selections.values())
        rows = (
            await session.execute(
                select(ConnectorConnectionRecord, ConnectorRecord, ConnectorToolCatalogRecord)
                .join(
                    ConnectorRecord,
                    and_(
                        ConnectorRecord.id == ConnectorConnectionRecord.connector_id,
                        ConnectorRecord.organization_id == ConnectorConnectionRecord.organization_id,
                        ConnectorRecord.workspace_id == ConnectorConnectionRecord.workspace_id,
                    ),
                )
                .join(
                    ConnectorToolCatalogRecord,
                    and_(
                        ConnectorToolCatalogRecord.connector_connection_id == ConnectorConnectionRecord.id,
                        ConnectorToolCatalogRecord.digest_sha256 == ConnectorConnectionRecord.current_catalog_digest,
                    ),
                )
                .where(
                    ConnectorConnectionRecord.id.in_(source_ids),
                    ConnectorConnectionRecord.organization_id == organization_id,
                    ConnectorConnectionRecord.workspace_id == workspace_id,
                    ConnectorConnectionRecord.deleted_at.is_(None),
                )
            )
        ).all()
        by_id = {connection.id: (connection, connector, catalog) for connection, connector, catalog in rows}
        result: list[_PreparedSource] = []
        for alias, selection in sorted(selections.items()):
            path = f"connector_tools.{alias}"
            selected = by_id.get(selection.connector_connection_id)
            if selected is None:
                raise ConnectivitySelectionError("connector_connection_unavailable", path=path)
            connection, connector, catalog = selected
            _require_source_owner(
                actor=actor,
                owner_type=connection.owner_type,
                owner_id=connection.owner_id,
                path=path,
            )
            if connection.status != "ready" or connector.status != "active":
                raise ConnectivitySelectionError("connector_connection_unavailable", path=path)
            compatibility_profile = f"{connector.driver_key}@{connector.config_version}"
            if (
                catalog.connection_setup_generation != connection.setup_generation
                or catalog.connector_credential_generation != connector.credential_generation
                or catalog.compatibility_profile != compatibility_profile
            ):
                raise ConnectivitySelectionError("connector_catalog_incompatible", path=path)
            result.append(
                _PreparedSource(
                    source_kind="connector_connection",
                    alias=alias,
                    source_id=connection.id,
                    connector_id=connector.id,
                    exposure=selection.exposure,
                    catalog_digest=catalog.digest_sha256,
                    catalog_object_key=catalog.object_key,
                    catalog_size_bytes=catalog.size_bytes,
                    credential_generation=connector.credential_generation,
                    source_generation=connection.setup_generation,
                    compatibility_profile=compatibility_profile,
                    requested_tool_keys=selection.tools,
                )
            )
        return tuple(result)

    async def _mcp_sources(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        selections: Mapping[str, MCPConnectionToolSelection],
    ) -> tuple[_PreparedSource, ...]:
        if not selections:
            return ()
        await _authorize_source_kind(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.mcp_connection_read,
            path="mcp_tools",
        )
        source_ids = tuple(selection.mcp_connection_id for selection in selections.values())
        rows = (
            await session.execute(
                select(MCPConnectionRecord, MCPToolCatalogRecord)
                .join(
                    MCPToolCatalogRecord,
                    and_(
                        MCPToolCatalogRecord.mcp_connection_id == MCPConnectionRecord.id,
                        MCPToolCatalogRecord.digest_sha256 == MCPConnectionRecord.current_catalog_digest,
                    ),
                )
                .where(
                    MCPConnectionRecord.id.in_(source_ids),
                    MCPConnectionRecord.organization_id == organization_id,
                    MCPConnectionRecord.workspace_id == workspace_id,
                    MCPConnectionRecord.deleted_at.is_(None),
                )
            )
        ).all()
        by_id = {connection.id: (connection, catalog) for connection, catalog in rows}
        result: list[_PreparedSource] = []
        for alias, selection in sorted(selections.items()):
            path = f"mcp_tools.{alias}"
            selected = by_id.get(selection.mcp_connection_id)
            if selected is None:
                raise ConnectivitySelectionError("mcp_connection_unavailable", path=path)
            connection, catalog = selected
            _require_source_owner(
                actor=actor,
                owner_type="user" if connection.owner_user_id is not None else None,
                owner_id=connection.owner_user_id,
                path=path,
            )
            if connection.status != "ready":
                raise ConnectivitySelectionError("mcp_connection_unavailable", path=path)
            if catalog.credential_generation != connection.credential_generation:
                raise ConnectivitySelectionError("mcp_catalog_incompatible", path=path)
            result.append(
                _PreparedSource(
                    source_kind="mcp_connection",
                    alias=alias,
                    source_id=connection.id,
                    connector_id=None,
                    exposure=selection.exposure,
                    catalog_digest=catalog.digest_sha256,
                    catalog_object_key=catalog.object_key,
                    catalog_size_bytes=catalog.size_bytes,
                    credential_generation=connection.credential_generation,
                    source_generation=0,
                    compatibility_profile=catalog.protocol_revision,
                    requested_tool_keys=selection.tools,
                )
            )
        return tuple(result)

    async def _freeze_sources(
        self,
        session: AsyncSession,
        prepared: PreparedRevisionConnectivity,
    ) -> None:
        connector_sources = tuple(source for source in prepared.sources if source.source_kind == "connector_connection")
        mcp_sources = tuple(source for source in prepared.sources if source.source_kind == "mcp_connection")
        await self._freeze_connectors(session, prepared, connector_sources)
        await self._freeze_mcps(session, prepared, mcp_sources)

    async def _freeze_connectors(
        self,
        session: AsyncSession,
        prepared: PreparedRevisionConnectivity,
        sources: tuple[_SourceEvidence, ...],
    ) -> None:
        if not sources:
            return
        await _authorize_source_kind(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            action=WorkspaceAction.connector_connection_read,
            path="connector_tools",
        )
        rows = (
            await session.execute(
                select(ConnectorConnectionRecord, ConnectorRecord, ConnectorToolCatalogRecord)
                .join(
                    ConnectorRecord,
                    and_(
                        ConnectorRecord.id == ConnectorConnectionRecord.connector_id,
                        ConnectorRecord.organization_id == ConnectorConnectionRecord.organization_id,
                        ConnectorRecord.workspace_id == ConnectorConnectionRecord.workspace_id,
                    ),
                )
                .join(
                    ConnectorToolCatalogRecord,
                    and_(
                        ConnectorToolCatalogRecord.connector_connection_id == ConnectorConnectionRecord.id,
                        ConnectorToolCatalogRecord.digest_sha256 == ConnectorConnectionRecord.current_catalog_digest,
                    ),
                )
                .where(
                    ConnectorConnectionRecord.id.in_(tuple(source.source_id for source in sources)),
                    ConnectorConnectionRecord.organization_id == prepared.organization_id,
                    ConnectorConnectionRecord.workspace_id == prepared.workspace_id,
                    ConnectorConnectionRecord.deleted_at.is_(None),
                )
                .order_by(ConnectorConnectionRecord.id)
                .with_for_update()
            )
        ).all()
        by_id = {connection.id: (connection, connector, catalog) for connection, connector, catalog in rows}
        for source in sources:
            path = _source_path(source)
            selected = by_id.get(source.source_id)
            if selected is None:
                raise ConnectivitySelectionError("connector_connection_changed", path=path)
            connection, connector, catalog = selected
            _require_source_owner(
                actor=prepared.actor,
                owner_type=connection.owner_type,
                owner_id=connection.owner_id,
                path=path,
            )
            if (
                connection.status != "ready"
                or connection.current_catalog_digest != source.catalog_digest
                or connector.id != source.connector_id
                or connector.status != "active"
                or connector.credential_generation != source.credential_generation
                or connection.setup_generation != source.source_generation
                or f"{connector.driver_key}@{connector.config_version}" != source.compatibility_profile
                or catalog.digest_sha256 != source.catalog_digest
                or catalog.object_key != source.catalog_object_key
                or catalog.size_bytes != source.catalog_size_bytes
                or catalog.connector_credential_generation != source.credential_generation
                or catalog.connection_setup_generation != source.source_generation
                or catalog.compatibility_profile != source.compatibility_profile
            ):
                raise ConnectivitySelectionError("connector_connection_changed", path=path)

    async def _freeze_mcps(
        self,
        session: AsyncSession,
        prepared: PreparedRevisionConnectivity,
        sources: tuple[_SourceEvidence, ...],
    ) -> None:
        if not sources:
            return
        await _authorize_source_kind(
            session,
            actor=prepared.actor,
            workspace_id=prepared.workspace_id,
            action=WorkspaceAction.mcp_connection_read,
            path="mcp_tools",
        )
        rows = (
            await session.execute(
                select(MCPConnectionRecord, MCPToolCatalogRecord)
                .join(
                    MCPToolCatalogRecord,
                    and_(
                        MCPToolCatalogRecord.mcp_connection_id == MCPConnectionRecord.id,
                        MCPToolCatalogRecord.digest_sha256 == MCPConnectionRecord.current_catalog_digest,
                    ),
                )
                .where(
                    MCPConnectionRecord.id.in_(tuple(source.source_id for source in sources)),
                    MCPConnectionRecord.organization_id == prepared.organization_id,
                    MCPConnectionRecord.workspace_id == prepared.workspace_id,
                    MCPConnectionRecord.deleted_at.is_(None),
                )
                .order_by(MCPConnectionRecord.id)
                .with_for_update()
            )
        ).all()
        by_id = {connection.id: (connection, catalog) for connection, catalog in rows}
        for source in sources:
            path = _source_path(source)
            selected = by_id.get(source.source_id)
            if selected is None:
                raise ConnectivitySelectionError("mcp_connection_changed", path=path)
            connection, catalog = selected
            _require_source_owner(
                actor=prepared.actor,
                owner_type="user" if connection.owner_user_id is not None else None,
                owner_id=connection.owner_user_id,
                path=path,
            )
            if (
                connection.status != "ready"
                or connection.current_catalog_digest != source.catalog_digest
                or connection.credential_generation != source.credential_generation
                or catalog.digest_sha256 != source.catalog_digest
                or catalog.object_key != source.catalog_object_key
                or catalog.size_bytes != source.catalog_size_bytes
                or catalog.credential_generation != source.credential_generation
                or catalog.protocol_revision != source.compatibility_profile
            ):
                raise ConnectivitySelectionError("mcp_connection_changed", path=path)


async def _authorize_source_kind(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
    path: str,
) -> None:
    try:
        await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        raise ConnectivitySelectionError("connection_not_eligible", path=path) from error


def _require_source_owner(
    *,
    actor: AuthenticatedActor,
    owner_type: str | None,
    owner_id: str | None,
    path: str,
) -> None:
    if owner_type is not None and (
        actor.principal.principal_type.value != owner_type or actor.principal.principal_id != owner_id
    ):
        raise ConnectivitySelectionError("connection_not_eligible", path=path)


def _select_tools(
    catalog_tools: tuple[CatalogTool, ...],
    requested: tuple[str, ...] | None,
    *,
    path: str,
) -> tuple[CatalogTool, ...]:
    by_key = {tool.key: tool for tool in catalog_tools}
    if len(by_key) != len(catalog_tools):
        raise ConnectivitySelectionError("catalog_tool_duplicate", path=path)
    allowed = tuple(sorted(by_key)) if requested is None else tuple(sorted(requested))
    if any(key not in by_key for key in allowed):
        raise ConnectivitySelectionError("tool_not_found", path=path)
    return tuple(by_key[key] for key in allowed)


def _replace_tools(source: _PreparedSource, tools: tuple[CatalogTool, ...]) -> _SourceEvidence:
    return _SourceEvidence(
        source_kind=source.source_kind,
        alias=source.alias,
        source_id=source.source_id,
        connector_id=source.connector_id,
        exposure=source.exposure,
        allowed_tool_keys=tuple(tool.key for tool in tools),
        catalog_digest=source.catalog_digest,
        catalog_object_key=source.catalog_object_key,
        catalog_size_bytes=source.catalog_size_bytes,
        credential_generation=source.credential_generation,
        source_generation=source.source_generation,
        compatibility_profile=source.compatibility_profile,
        tools=tools,
    )


def _reject_duplicate_sources(
    connector_tools: Mapping[str, ConnectorConnectionToolSelection],
    mcp_tools: Mapping[str, MCPConnectionToolSelection],
) -> None:
    for path, identifiers in (
        ("connector_tools", tuple(item.connector_connection_id for item in connector_tools.values())),
        ("mcp_tools", tuple(item.mcp_connection_id for item in mcp_tools.values())),
    ):
        if len(identifiers) != len(set(identifiers)):
            raise ConnectivitySelectionError("connection_selected_more_than_once", path=path)


def _required_connector_id(source: _SourceEvidence) -> str:
    if source.connector_id is None:
        raise AssertionError("Connector source is missing its Connector identity")
    return source.connector_id


def _source_path(source: _SourceIdentity) -> str:
    prefix = "connector_tools" if source.source_kind == "connector_connection" else "mcp_tools"
    return f"{prefix}.{source.alias}"


__all__ = [
    "ConnectivitySelectionError",
    "ConnectivitySelectionResolver",
    "FrozenRunConnectivity",
    "PreparedRevisionConnectivity",
    "PreparedRunConnectivity",
]
