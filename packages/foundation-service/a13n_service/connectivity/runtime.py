"""Typed Connectivity capabilities owned by each process role."""

from __future__ import annotations

from dataclasses import dataclass

from a13n_service.connectivity.adapters import ConnectorAdapter, IngressAdapter
from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.connectors.catalog import ConnectorCatalogService
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.connectors.service import ConnectorService
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.routes import RouteService
from a13n_service.connectivity.ingress.service import IngressService
from a13n_service.connectivity.mcp.catalog_service import MCPCatalogService
from a13n_service.connectivity.mcp.oauth_client import MCPOAuthClient
from a13n_service.connectivity.mcp.oauth_service import MCPOAuthService
from a13n_service.connectivity.mcp.service import MCPConnectionService
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.secrets import InternalSecretService


@dataclass(frozen=True, slots=True)
class ConnectivityControlRuntime:
    """Connectivity management capabilities owned by Control-plane roles."""

    public_origin: str
    connector_adapters: AdapterRegistry[ConnectorAdapter]
    endpoint_policy: EndpointPolicy
    selection_resolver: ConnectivitySelectionResolver
    ingresses: IngressService
    routes: RouteService
    connectors: ConnectorService
    connector_connections: ConnectorConnectionService
    connector_catalog: ConnectorCatalogService
    mcp_catalog: MCPCatalogService
    mcp_oauth_client: MCPOAuthClient
    mcp_connections: MCPConnectionService
    mcp_oauth: MCPOAuthService


@dataclass(frozen=True, slots=True)
class ConnectivityDataRuntime:
    """Provider-ingress capabilities owned by Connectivity data-plane roles."""

    ingress_events: IngressEventService


@dataclass(frozen=True, slots=True)
class ConnectivityRuntime:
    """Shared Connectivity resources and optional role-specific capabilities."""

    ingress_adapters: AdapterRegistry[IngressAdapter]
    internal_secrets: InternalSecretService
    control: ConnectivityControlRuntime | None
    data: ConnectivityDataRuntime | None


__all__ = [
    "ConnectivityControlRuntime",
    "ConnectivityDataRuntime",
    "ConnectivityRuntime",
]
