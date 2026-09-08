"""Agent-owned integration with Connectivity selection resolution."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.selection_resolution import (
    ConnectivitySelectionError,
    ConnectivitySelectionResolver,
    FrozenRunConnectivity,
    PreparedConnectivity,
)
from a13n_service.iam import AuthenticatedActor

from .domain import ConnectorConnectionToolSelection, MCPConnectionToolSelection
from .errors import agent_revision_create_failed, agent_revision_not_executable


class _ConnectivityConfig(Protocol):
    @property
    def connector_tools(self) -> tuple[ConnectorConnectionToolSelection, ...]: ...

    @property
    def mcp_tools(self) -> tuple[MCPConnectionToolSelection, ...]: ...


async def prepare_revision_connectivity(
    resolver: ConnectivitySelectionResolver,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    config: _ConnectivityConfig,
) -> PreparedConnectivity:
    try:
        return await resolver.prepare(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_tools=config.connector_tools,
            mcp_tools=config.mcp_tools,
        )
    except ConnectivitySelectionError as error:
        raise agent_revision_create_failed(error.code, path=error.path) from error


async def freeze_revision_connectivity(
    resolver: ConnectivitySelectionResolver,
    session: AsyncSession,
    prepared: PreparedConnectivity,
) -> None:
    try:
        await resolver.freeze(session, prepared=prepared)
    except ConnectivitySelectionError as error:
        raise agent_revision_create_failed(error.code, path=error.path) from error


async def prepare_invocation_connectivity(
    resolver: ConnectivitySelectionResolver,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    config: _ConnectivityConfig,
) -> PreparedConnectivity:
    try:
        return await resolver.prepare(
            actor=actor,
            organization_id=organization_id,
            workspace_id=workspace_id,
            connector_tools=config.connector_tools,
            mcp_tools=config.mcp_tools,
        )
    except ConnectivitySelectionError as error:
        raise agent_revision_not_executable(error.code) from error


async def freeze_invocation_connectivity(
    resolver: ConnectivitySelectionResolver,
    session: AsyncSession,
    prepared: PreparedConnectivity,
) -> FrozenRunConnectivity:
    try:
        return await resolver.freeze(session, prepared=prepared)
    except ConnectivitySelectionError as error:
        raise agent_revision_not_executable(error.code) from error


__all__ = [
    "freeze_invocation_connectivity",
    "freeze_revision_connectivity",
    "prepare_invocation_connectivity",
    "prepare_revision_connectivity",
]
