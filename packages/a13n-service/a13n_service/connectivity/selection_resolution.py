"""Authorize accepted connection selections without external discovery."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.connectors.models import ConnectorConnectionRecord, ConnectorProviderRecord
from a13n_service.connectivity.mcp.models import MCPConnectionRecord
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_workspace
from a13n_service.iam.authorization import PrincipalPermissions
from a13n_service.iam.resource_scope import visible_workspace
from a13n_service.storage import short_session

from .selection_domain import (
    ConnectorConnectionRunSelection,
    ConnectorConnectionToolSelection,
    MCPConnectionToolSelection,
)


class ConnectivitySelectionError(RuntimeError):
    def __init__(self, code: str, *, path: str) -> None:
        super().__init__(code)
        self.code = code
        self.path = path


@dataclass(frozen=True, slots=True)
class FrozenRunConnectivity:
    connector_connection_selections: tuple[ConnectorConnectionRunSelection, ...]
    mcp_connection_selections: tuple[MCPConnectionToolSelection, ...]


@dataclass(frozen=True, slots=True)
class PreparedConnectivity:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    selections: FrozenRunConnectivity


class ConnectivitySelectionResolver:
    """Recheck current resource authority in the short acceptance transaction."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def prepare(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        connector_tools: tuple[ConnectorConnectionToolSelection, ...],
        mcp_tools: tuple[MCPConnectionToolSelection, ...],
    ) -> PreparedConnectivity:
        async with short_session(self._sessions) as session:
            selections = await self.resolve_in_session(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                connector_tools=connector_tools,
                mcp_tools=mcp_tools,
            )
        return PreparedConnectivity(actor, organization_id, workspace_id, selections)

    async def freeze(self, session: AsyncSession, *, prepared: PreparedConnectivity) -> FrozenRunConnectivity:
        current = await self.resolve_in_session(
            session,
            actor=prepared.actor,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            connector_tools=prepared.selections.connector_connection_selections,
            mcp_tools=prepared.selections.mcp_connection_selections,
            lock=True,
        )
        if current != prepared.selections:
            raise ConnectivitySelectionError("connection_changed", path="connectivity")
        return prepared.selections

    async def require_current_source(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        selection: ConnectorConnectionRunSelection | MCPConnectionToolSelection,
        snapshot: PrincipalPermissions | None = None,
    ) -> None:
        expected = (
            FrozenRunConnectivity((selection,), ())
            if isinstance(selection, ConnectorConnectionRunSelection)
            else FrozenRunConnectivity((), (selection,))
        )
        current = await self.resolve_in_session(
            session,
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_tools=expected.connector_connection_selections,
            mcp_tools=expected.mcp_connection_selections,
            snapshot=snapshot,
        )
        if current != expected:
            raise ConnectivitySelectionError("connection_changed", path="connectivity")

    @staticmethod
    async def resolve_in_session(
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        connector_tools: tuple[ConnectorConnectionToolSelection, ...],
        mcp_tools: tuple[MCPConnectionToolSelection, ...],
        lock: bool = False,
        snapshot: PrincipalPermissions | None = None,
    ) -> FrozenRunConnectivity:
        for path, identifiers, action in (
            (
                "connector_tools",
                tuple(item.connector_connection_id for item in connector_tools),
                WorkspaceAction.connector_connection_read,
            ),
            ("mcp_tools", tuple(item.mcp_connection_id for item in mcp_tools), WorkspaceAction.mcp_connection_read),
        ):
            if len(identifiers) != len(set(identifiers)):
                raise ConnectivitySelectionError("connection_selected_more_than_once", path=path)
            if identifiers:
                try:
                    await authorize_workspace(
                        session, actor=actor, workspace_id=workspace_id, action=action, snapshot=snapshot
                    )
                except AuthorizationError as error:
                    raise ConnectivitySelectionError("connection_not_eligible", path=path) from error
        connectors: list[ConnectorConnectionRunSelection] = []
        if connector_tools:
            query = (
                select(ConnectorConnectionRecord, ConnectorProviderRecord)
                .join(
                    ConnectorProviderRecord,
                    and_(
                        ConnectorProviderRecord.id == ConnectorConnectionRecord.connector_provider_id,
                        ConnectorProviderRecord.organization_id == ConnectorConnectionRecord.organization_id,
                        visible_workspace(ConnectorProviderRecord.workspace_id, workspace_id),
                    ),
                )
                .where(
                    ConnectorConnectionRecord.id.in_(item.connector_connection_id for item in connector_tools),
                    ConnectorConnectionRecord.organization_id == organization_id,
                    ConnectorConnectionRecord.workspace_id == workspace_id,
                    ConnectorConnectionRecord.deleted_at.is_(None),
                )
                .order_by(ConnectorConnectionRecord.id)
            )
            # Freeze selected configuration against edits, not other admissions.
            rows = (await session.execute(query.with_for_update(read=True) if lock else query)).all()
            by_id = {connection.id: (connection, provider) for connection, provider in rows}
            for index, selection in enumerate(connector_tools):
                path = f"connector_tools.{index}"
                row = by_id.get(selection.connector_connection_id)
                if row is None:
                    raise ConnectivitySelectionError("connector_connection_unavailable", path=path)
                connection, provider = row
                if connection.status != "ready" or provider.status != "active":
                    raise ConnectivitySelectionError("connector_connection_unavailable", path=path)
                connectors.append(
                    ConnectorConnectionRunSelection(
                        connector_connection_id=connection.id,
                        connector_provider_id=provider.id,
                        tools=selection.tools,
                        defer_loading=selection.defer_loading,
                    )
                )
        mcps: list[MCPConnectionToolSelection] = []
        if mcp_tools:
            query = (
                select(MCPConnectionRecord)
                .where(
                    MCPConnectionRecord.id.in_(item.mcp_connection_id for item in mcp_tools),
                    MCPConnectionRecord.organization_id == organization_id,
                    MCPConnectionRecord.workspace_id == workspace_id,
                    MCPConnectionRecord.deleted_at.is_(None),
                )
                .order_by(MCPConnectionRecord.id)
            )
            rows = (await session.scalars(query.with_for_update() if lock else query)).all()
            by_id = {connection.id: connection for connection in rows}
            for index, selection in enumerate(mcp_tools):
                path = f"mcp_tools.{index}"
                connection = by_id.get(selection.mcp_connection_id)
                if connection is None or connection.status != "ready":
                    raise ConnectivitySelectionError("mcp_connection_unavailable", path=path)
                mcps.append(
                    MCPConnectionToolSelection(
                        mcp_connection_id=connection.id,
                        tools=selection.tools,
                        defer_loading=selection.defer_loading,
                    )
                )
        return FrozenRunConnectivity(tuple(connectors), tuple(mcps))
