"""Authorize accepted connection selections without external discovery."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.connectivity.connections.models import ConnectionRecord
from a13n_service.connectivity.connectors.models import ConnectorConnectionRecord, ConnectorProviderRecord
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_workspace
from a13n_service.iam.authorization import PrincipalPermissions
from a13n_service.iam.resource_scope import visible_workspace
from a13n_service.storage import short_session

from .selection_domain import (
    ConnectionRunSelection,
    ConnectionToolSelection,
)


class ConnectivitySelectionError(RuntimeError):
    def __init__(self, code: str, *, path: str) -> None:
        super().__init__(code)
        self.code = code
        self.path = path


@dataclass(frozen=True, slots=True)
class FrozenRunConnectivity:
    connection_selections: tuple[ConnectionRunSelection, ...]


@dataclass(frozen=True, slots=True)
class PreparedConnectivity:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    selections: FrozenRunConnectivity


class ConnectivitySelectionResolver:
    """Freeze connection identity and authorization at Run acceptance."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def prepare(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        connection_tools: tuple[ConnectionToolSelection, ...],
    ) -> PreparedConnectivity:
        async with short_session(self._sessions) as session:
            selections = await self.resolve_in_session(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                connection_tools=connection_tools,
            )
        return PreparedConnectivity(actor, organization_id, workspace_id, selections)

    async def freeze(self, session: AsyncSession, *, prepared: PreparedConnectivity) -> FrozenRunConnectivity:
        current = await self.resolve_in_session(
            session,
            actor=prepared.actor,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            connection_tools=prepared.selections.connection_selections,
            lock=True,
        )
        if current != prepared.selections:
            raise ConnectivitySelectionError("connection_changed", path="connection_tools")
        return prepared.selections

    async def require_current_source(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        selection: ConnectionRunSelection,
        snapshot: PrincipalPermissions | None = None,
    ) -> None:
        current = await self.resolve_in_session(
            session,
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            connection_tools=(selection,),
            snapshot=snapshot,
        )
        if current != FrozenRunConnectivity((selection,)):
            raise ConnectivitySelectionError("connection_changed", path="connection_tools")

    @staticmethod
    async def resolve_in_session(
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        connection_tools: tuple[ConnectionToolSelection, ...],
        lock: bool = False,
        snapshot: PrincipalPermissions | None = None,
    ) -> FrozenRunConnectivity:
        identifiers = tuple(item.connection_id for item in connection_tools)
        if len(identifiers) != len(set(identifiers)):
            raise ConnectivitySelectionError("connection_selected_more_than_once", path="connection_tools")
        if not identifiers:
            return FrozenRunConnectivity(())
        try:
            await authorize_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.connection_read,
                snapshot=snapshot,
            )
        except AuthorizationError as error:
            raise ConnectivitySelectionError("connection_not_eligible", path="connection_tools") from error
        query = (
            select(ConnectionRecord)
            .where(
                ConnectionRecord.id.in_(identifiers),
                ConnectionRecord.organization_id == organization_id,
                ConnectionRecord.workspace_id == workspace_id,
                ConnectionRecord.deleted_at.is_(None),
            )
            .order_by(ConnectionRecord.id)
        )
        rows = (await session.scalars(query.with_for_update(read=True) if lock else query)).all()
        by_id = {connection.id: connection for connection in rows}
        provider_ids = {
            connection.connector_provider_id for connection in rows if isinstance(connection, ConnectorConnectionRecord)
        }
        providers = {}
        if provider_ids:
            provider_query = (
                select(ConnectorProviderRecord)
                .where(
                    ConnectorProviderRecord.id.in_(provider_ids),
                    ConnectorProviderRecord.organization_id == organization_id,
                    visible_workspace(ConnectorProviderRecord.workspace_id, workspace_id),
                )
                .order_by(ConnectorProviderRecord.id)
            )
            providers = {
                provider.id: provider
                for provider in (
                    await session.scalars(provider_query.with_for_update(read=True) if lock else provider_query)
                ).all()
            }
        selections = []
        for index, selection in enumerate(connection_tools):
            connection = by_id.get(selection.connection_id)
            path = f"connection_tools.{index}"
            if connection is None or connection.status != "ready":
                raise ConnectivitySelectionError("connection_unavailable", path=path)
            provider_id = None
            if isinstance(connection, ConnectorConnectionRecord):
                provider = providers.get(connection.connector_provider_id)
                if provider is None or provider.status != "active":
                    raise ConnectivitySelectionError("connection_unavailable", path=path)
                provider_id = provider.id
            selections.append(
                ConnectionRunSelection(
                    connection_id=connection.id,
                    kind="connector" if isinstance(connection, ConnectorConnectionRecord) else "mcp",
                    connector_provider_id=provider_id,
                    authorization_generation=connection.authorization_generation,
                    tools=selection.tools,
                    defer_loading=selection.defer_loading,
                    permissions=selection.permissions,
                )
            )
        return FrozenRunConnectivity(tuple(selections))
