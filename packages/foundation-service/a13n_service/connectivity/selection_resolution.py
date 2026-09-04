"""Authorize accepted connection selections without external discovery."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import AccountToolSelection
from a13n_service.connectivity.accounts.models import AccountRecord
from a13n_service.connectivity.accounts.targets import validate_scope
from a13n_service.connectivity.connectors.models import ConnectorConnectionRecord, ConnectorProviderRecord
from a13n_service.connectivity.mcp.models import MCPConnectionRecord
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_workspace
from a13n_service.storage import short_session

from .selection_domain import (
    ConnectorConnectionRunSelection,
    ConnectorConnectionToolSelection,
    MCPConnectionRunSelection,
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
    mcp_connection_selections: tuple[MCPConnectionRunSelection, ...]
    account_selections: tuple[AccountToolSelection, ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedRevisionConnectivity:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    selections: FrozenRunConnectivity


@dataclass(frozen=True, slots=True)
class PreparedRunConnectivity(PreparedRevisionConnectivity):
    run_id: str


class ConnectivitySelectionResolver:
    """Recheck current resource authority in the short acceptance transaction."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def prepare_revision_creation(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        connector_tools: tuple[ConnectorConnectionToolSelection, ...],
        mcp_tools: tuple[MCPConnectionToolSelection, ...],
        account_tools: tuple[AccountToolSelection, ...] = (),
    ) -> PreparedRevisionConnectivity:
        async with short_session(self._sessions) as session:
            selections = await self._resolve(
                session,
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                connector_tools=connector_tools,
                mcp_tools=mcp_tools,
                account_tools=account_tools,
            )
        return PreparedRevisionConnectivity(actor, organization_id, workspace_id, selections)

    async def freeze_revision_creation(self, session: AsyncSession, *, prepared: PreparedRevisionConnectivity) -> None:
        current = await self._resolve(
            session,
            actor=prepared.actor,
            organization_id=prepared.organization_id,
            workspace_id=prepared.workspace_id,
            connector_tools=prepared.selections.connector_connection_selections,
            mcp_tools=prepared.selections.mcp_connection_selections,
            account_tools=prepared.selections.account_selections,
            lock=True,
        )
        if current != prepared.selections:
            raise ConnectivitySelectionError("connection_changed", path="connectivity")

    async def prepare_invocation(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        run_id: str,
        connector_tools: tuple[ConnectorConnectionToolSelection, ...],
        mcp_tools: tuple[MCPConnectionToolSelection, ...],
        account_tools: tuple[AccountToolSelection, ...] = (),
    ) -> PreparedRunConnectivity:
        prepared = await self.prepare_revision_creation(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_tools=connector_tools,
            mcp_tools=mcp_tools,
            account_tools=account_tools,
        )
        return PreparedRunConnectivity(actor, organization_id, workspace_id, prepared.selections, run_id)

    async def freeze_invocation(
        self, session: AsyncSession, *, prepared: PreparedRunConnectivity
    ) -> FrozenRunConnectivity:
        await self.freeze_revision_creation(session, prepared=prepared)
        return prepared.selections

    async def _resolve(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        connector_tools: tuple[ConnectorConnectionToolSelection, ...],
        mcp_tools: tuple[MCPConnectionToolSelection, ...],
        account_tools: tuple[AccountToolSelection, ...] = (),
        lock: bool = False,
    ) -> FrozenRunConnectivity:
        for path, identifiers, action in (
            (
                "account_tools",
                tuple(item.account_id for item in account_tools),
                WorkspaceAction.application_account_use,
            ),
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
                    await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
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
                        ConnectorProviderRecord.workspace_id == ConnectorConnectionRecord.workspace_id,
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
            rows = (await session.execute(query.with_for_update() if lock else query)).all()
            by_id = {connection.id: (connection, provider) for connection, provider in rows}
            for index, selection in enumerate(connector_tools):
                path = f"connector_tools.{index}"
                row = by_id.get(selection.connector_connection_id)
                if row is None:
                    raise ConnectivitySelectionError("connector_connection_unavailable", path=path)
                connection, provider = row
                require_source_owner(actor, connection.owner_type, connection.owner_id, path=path)
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
        mcps: list[MCPConnectionRunSelection] = []
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
                require_source_owner(
                    actor, "user" if connection.owner_user_id else None, connection.owner_user_id, path=path
                )
                mcps.append(
                    MCPConnectionRunSelection(
                        mcp_connection_id=connection.id,
                        tools=selection.tools,
                        defer_loading=selection.defer_loading,
                    )
                )
        accounts: list[AccountToolSelection] = []
        if account_tools:
            query = (
                select(AccountRecord)
                .where(
                    AccountRecord.id.in_(item.account_id for item in account_tools),
                    AccountRecord.organization_id == organization_id,
                    AccountRecord.workspace_id == workspace_id,
                    AccountRecord.deleted_at.is_(None),
                    AccountRecord.status == "active",
                )
                .order_by(AccountRecord.id)
            )
            rows = (await session.scalars(query.with_for_update() if lock else query)).all()
            by_id = {account.id: account for account in rows}
            for index, selection in enumerate(account_tools):
                path = f"account_tools.{index}"
                account = by_id.get(selection.account_id)
                if account is None:
                    raise ConnectivitySelectionError("account_unavailable", path=path)
                try:
                    validate_scope(account.provider_key, selection.target_scope, selection.tools)
                except ValueError as error:
                    raise ConnectivitySelectionError("invalid_account_scope", path=path) from error
                accounts.append(selection)
        return FrozenRunConnectivity(tuple(connectors), tuple(mcps), tuple(accounts))


def require_source_owner(actor: AuthenticatedActor, owner_type: str | None, owner_id: str | None, *, path: str) -> None:
    if owner_type is not None and (
        actor.principal.principal_type.value != owner_type or actor.principal.principal_id != owner_id
    ):
        raise ConnectivitySelectionError("connection_not_eligible", path=path)
