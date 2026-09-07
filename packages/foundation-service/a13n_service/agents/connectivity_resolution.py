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
    resolver: ConnectivitySelectionResolver | None,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    config: _ConnectivityConfig,
) -> PreparedConnectivity | None:
    if not config.connector_tools and not config.mcp_tools:
        return None
    if resolver is None:
        if config.connector_tools:
            raise agent_revision_create_failed("connector_tool_resolution_unavailable", path="connector_tools")
        raise agent_revision_create_failed("mcp_tool_resolution_unavailable", path="mcp_tools")
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
    resolver: ConnectivitySelectionResolver | None,
    session: AsyncSession,
    prepared: PreparedConnectivity | None,
) -> None:
    if prepared is None:
        return
    if resolver is None:
        raise agent_revision_create_failed("connectivity_resolution_unavailable", path="connectivity")
    try:
        await resolver.freeze(session, prepared=prepared)
    except ConnectivitySelectionError as error:
        raise agent_revision_create_failed(error.code, path=error.path) from error


async def prepare_invocation_connectivity(
    resolver: ConnectivitySelectionResolver | None,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    config: _ConnectivityConfig,
) -> PreparedConnectivity | None:
    if not config.connector_tools and not config.mcp_tools:
        return None
    if resolver is None:
        reason = (
            "connector_tool_resolution_unavailable" if config.connector_tools else "mcp_tool_resolution_unavailable"
        )
        raise agent_revision_not_executable(reason)
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
    resolver: ConnectivitySelectionResolver | None,
    session: AsyncSession,
    prepared: PreparedConnectivity | None,
) -> FrozenRunConnectivity | None:
    if prepared is None:
        return None
    if resolver is None:
        raise agent_revision_not_executable("connectivity_resolution_unavailable")
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
