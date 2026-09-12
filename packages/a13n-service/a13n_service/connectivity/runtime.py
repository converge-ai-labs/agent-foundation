"""Typed Connectivity capabilities owned by each process role."""

from __future__ import annotations

from dataclasses import dataclass

from a13n_service.connectivity.accounts.service import AccountService
from a13n_service.connectivity.accounts.target_service import AccountTargetService
from a13n_service.connectivity.connections.authorization import AuthorizationService
from a13n_service.connectivity.connections.checks import ConnectionChecks
from a13n_service.connectivity.connections.service import ConnectionService
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.connectors.service import ConnectorProviderService
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.mcp.oauth_service import MCPOAuthService
from a13n_service.connectivity.mcp.service import MCPConnectionService


@dataclass(frozen=True, slots=True)
class ConnectivityControlRuntime:
    """Connectivity management capabilities owned by Control-plane roles."""

    public_origin: str | None
    accounts: AccountService
    targets: AccountTargetService
    connector_providers: ConnectorProviderService
    connector_connections: ConnectorConnectionService
    mcp_connections: MCPConnectionService
    mcp_oauth: MCPOAuthService
    checks: ConnectionChecks
    connections: ConnectionService
    authorizations: AuthorizationService


@dataclass(frozen=True, slots=True)
class ConnectivityDataRuntime:
    """Provider-ingress capabilities owned by Connectivity data-plane roles."""

    ingress_events: IngressEventService


@dataclass(frozen=True, slots=True)
class ConnectivityRuntime:
    """Request-facing Connectivity capabilities owned by the process."""

    control: ConnectivityControlRuntime | None
    data: ConnectivityDataRuntime | None


__all__ = [
    "ConnectivityControlRuntime",
    "ConnectivityDataRuntime",
    "ConnectivityRuntime",
]
