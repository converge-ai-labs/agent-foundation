"""Connectivity composition for a13n Service process roles."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass

import httpx2
from a13n_harness.memory_plugins import MemoryBackendCatalog

from a13n_service.connectivity.accounts.service import AccountService
from a13n_service.connectivity.accounts.target_service import AccountTargetService
from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.bots.service import BotService
from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.connections.authorization import AuthorizationService
from a13n_service.connectivity.connections.checks import ConnectionChecks
from a13n_service.connectivity.connections.service import ConnectionService
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.connectors.http import ConnectorHttpClient
from a13n_service.connectivity.connectors.reconciler import ConnectorReconciler
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.connectors.service import ConnectorProviderService
from a13n_service.connectivity.http import cookie_free_jar
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.admission_domain import InputAcceptor
from a13n_service.connectivity.ingress.reconciler import IngressAdmissionReconciler
from a13n_service.connectivity.ingress.retention import IngressRetentionReconciler
from a13n_service.connectivity.mcp.catalog import MCPServerCatalog
from a13n_service.connectivity.mcp.discovery import MCPDiscoveryService
from a13n_service.connectivity.mcp.oauth_client import MCPOAuthClient
from a13n_service.connectivity.mcp.oauth_service import MCPOAuthService
from a13n_service.connectivity.mcp.reconciler import MCPReconciler
from a13n_service.connectivity.mcp.service import MCPConnectionService
from a13n_service.connectivity.runtime import (
    ConnectivityControlRuntime,
    ConnectivityDataRuntime,
    ConnectivityRuntime,
)
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.ids import new_object_id
from a13n_service.process.background import BackgroundTask
from a13n_service.provider_plugins import ProviderCatalogs
from a13n_service.provider_plugins.connectors import build_connector_provider_registry
from a13n_service.secrets import SecretProtector
from a13n_service.settings import Settings
from a13n_service.storage import StorageResources

from .connectivity_clients import build_mcp_clients, connectivity_http_timeout

logger = logging.getLogger("a13n_service.process.connectivity")


@dataclass(frozen=True, slots=True)
class _ConnectorControl:
    service: ConnectorProviderService
    connections: ConnectorConnectionService
    reconciler: ConnectorReconciler


@dataclass(frozen=True, slots=True)
class _MCPControl:
    discovery: MCPDiscoveryService
    oauth_client: MCPOAuthClient
    connections: MCPConnectionService
    oauth: MCPOAuthService
    reconciler: MCPReconciler


async def build_connectivity_runtime(
    settings: Settings,
    storage: StorageResources,
    secret_protector: SecretProtector,
    stack: AsyncExitStack,
    *,
    ingress_adapters: AdapterRegistry[IngressAdapter] | None,
    connector_providers: ConnectorProviderRegistry | None,
    provider_catalogs: ProviderCatalogs,
    input_acceptor: InputAcceptor | None,
    control_plane: bool,
    data_plane: bool,
    memory_catalog: MemoryBackendCatalog | None = None,
) -> tuple[ConnectivityRuntime | None, tuple[BackgroundTask, ...]]:
    """Construct only the Connectivity capabilities owned by this role."""

    if not control_plane and not data_plane:
        return None, ()
    if ingress_adapters is None:
        raise RuntimeError("Connectivity ingress adapters were not prepared")
    control, control_components = (
        await _build_control_runtime(
            settings,
            storage,
            ingress_adapters,
            connector_providers,
            provider_catalogs,
            secret_protector,
            stack,
            memory_catalog,
        )
        if control_plane
        else (None, ())
    )
    data, data_components = (
        _build_data_runtime(settings, input_acceptor, storage, ingress_adapters, secret_protector)
        if data_plane
        else (None, ())
    )
    return (
        ConnectivityRuntime(
            control=control,
            data=data,
        ),
        (*control_components, *data_components),
    )


async def _build_control_runtime(
    settings: Settings,
    storage: StorageResources,
    ingress_adapters: AdapterRegistry[IngressAdapter],
    connector_providers: ConnectorProviderRegistry | None,
    provider_catalogs: ProviderCatalogs,
    secret_protector: SecretProtector,
    stack: AsyncExitStack,
    memory_catalog: MemoryBackendCatalog | None,
) -> tuple[ConnectivityControlRuntime, tuple[BackgroundTask, ...]]:
    public_origin = settings.validated_connectivity_public_origin() if settings.connectivity.public_origin else None
    endpoint_policy = settings.connectivity_endpoint_policy()
    if connector_providers is None:
        connector_http_client = await stack.enter_async_context(
            httpx2.AsyncClient(
                cookies=cookie_free_jar(),
                follow_redirects=False,
                timeout=connectivity_http_timeout(settings),
            )
        )
        connector_providers = build_connector_provider_registry(
            provider_catalogs.connector,
            ConnectorHttpClient(
                connector_http_client,
                endpoint_policy,
                response_max_bytes=settings.connectivity.response_max_bytes,
                timeout_seconds=settings.connectivity.total_timeout_seconds,
            ),
        )
    connector = _build_connector_control(
        settings,
        storage,
        connector_providers,
        secret_protector,
        public_origin,
    )
    mcp_servers = MCPServerCatalog(settings.connectivity.mcp_servers, endpoint_policy)
    mcp_http_client = await stack.enter_async_context(
        httpx2.AsyncClient(
            cookies=cookie_free_jar(),
            follow_redirects=False,
            timeout=connectivity_http_timeout(settings),
        )
    )
    mcp = _build_mcp_control(
        settings,
        storage,
        endpoint_policy,
        secret_protector,
        mcp_http_client,
        mcp_servers,
    )
    accounts = AccountService(
        storage.sessions,
        ingress_adapters,
        secret_protector,
        memory_catalog=memory_catalog,
        batch_max_events=settings.connectivity.batch_max_events,
        batch_max_wait_seconds=settings.connectivity.batch_max_wait_seconds,
    )
    runtime = ConnectivityControlRuntime(
        public_origin=public_origin,
        bots=BotService(
            storage.sessions,
            mcp_http_client,
            endpoint_policy,
            secret_protector,
            public_origin=public_origin,
            accounts=accounts,
            timeout_seconds=settings.connectivity.total_timeout_seconds,
        ),
        accounts=accounts,
        targets=AccountTargetService(
            storage.sessions,
            ingress_adapters,
            batch_max_events=settings.connectivity.batch_max_events,
            batch_max_wait_seconds=settings.connectivity.batch_max_wait_seconds,
        ),
        connector_providers=connector.service,
        connector_connections=connector.connections,
        mcp_connections=mcp.connections,
        mcp_servers=mcp_servers,
        mcp_oauth=mcp.oauth,
        checks=ConnectionChecks(storage.sessions, connector_providers, secret_protector, mcp.connections),
        connections=ConnectionService(storage.sessions, endpoint_policy),
        authorizations=AuthorizationService(
            storage.sessions,
            secret_protector,
            connector.connections,
            mcp.oauth,
            mcp.connections,
            public_origin=public_origin,
            callback_urls=settings.connectivity.authorization_callback_urls,
        ),
    )
    background_components = (
        BackgroundTask("connector reconciler", connector.reconciler.run),
        BackgroundTask("MCP reconciler", mcp.reconciler.run),
    )
    return runtime, background_components


def _build_connector_control(
    settings: Settings,
    storage: StorageResources,
    connector_providers: ConnectorProviderRegistry,
    secret_protector: SecretProtector,
    public_origin: str | None,
) -> _ConnectorControl:
    service = ConnectorProviderService(storage.sessions, connector_providers, secret_protector)
    correlation_secret = settings.connectivity.setup_correlation_secret
    connections = ConnectorConnectionService(
        storage.sessions,
        connector_providers,
        secret_protector,
        correlation_secret=(correlation_secret.get_secret_value().encode() if correlation_secret is not None else None),
        public_origin=public_origin,
        setup_ttl_seconds=settings.connectivity.oauth_setup_ttl_seconds,
        setup_lease_seconds=settings.connectivity.connector_reconcile_lease_seconds,
    )
    instance_id = settings.service.instance_id or new_object_id("svc")
    reconciler = ConnectorReconciler(
        storage.sessions,
        connector_providers,
        connections.setup_coordinator,
        instance_id=instance_id,
        poll_interval_seconds=settings.connectivity.connector_reconcile_poll_interval_seconds,
        lease_seconds=settings.connectivity.connector_reconcile_lease_seconds,
    )
    return _ConnectorControl(service=service, connections=connections, reconciler=reconciler)


def _build_mcp_control(
    settings: Settings,
    storage: StorageResources,
    endpoint_policy: EndpointPolicy,
    secret_protector: SecretProtector,
    http_client: httpx2.AsyncClient,
    mcp_servers: MCPServerCatalog,
) -> _MCPControl:
    instance_id = settings.service.instance_id or new_object_id("svc")
    clients = build_mcp_clients(settings, storage.sessions, secret_protector, http_client, endpoint_policy)
    discovery = MCPDiscoveryService(storage.sessions, clients.transport, clients.credentials)
    connections = MCPConnectionService(
        storage.sessions,
        endpoint_policy,
        secret_protector,
        discovery,
        registration_cleaner=clients.oauth,
    )
    oauth = MCPOAuthService(
        storage.sessions,
        clients.oauth,
        secret_protector,
        discovery,
        redirect_uris=settings.connectivity.authorization_callback_urls,
        documentation_urls=mcp_servers.documentation_urls(),
        client_name=settings.connectivity.oauth_client_name,
        instance_id=instance_id,
        setup_ttl_seconds=settings.connectivity.oauth_setup_ttl_seconds,
        claim_lease_seconds=settings.connectivity.connector_reconcile_lease_seconds,
    )
    reconciler = MCPReconciler(
        storage.sessions,
        poll_interval_seconds=settings.connectivity.connector_reconcile_poll_interval_seconds,
    )
    return _MCPControl(
        discovery=discovery,
        oauth_client=clients.oauth,
        connections=connections,
        oauth=oauth,
        reconciler=reconciler,
    )


def _build_data_runtime(
    settings: Settings,
    input_acceptor: InputAcceptor | None,
    storage: StorageResources,
    ingress_adapters: AdapterRegistry[IngressAdapter],
    secret_protector: SecretProtector,
) -> tuple[ConnectivityDataRuntime, tuple[BackgroundTask, ...]]:
    ingress_events = IngressEventService(
        storage.sessions,
        ingress_adapters,
        secret_protector,
        request_max_bytes=settings.connectivity.provider_request_max_bytes,
        workspace_pending_max_count=settings.connectivity.workspace_pending_max_count,
        workspace_pending_max_bytes=settings.connectivity.workspace_pending_max_bytes,
        account_pending_max_count=settings.connectivity.account_pending_max_count,
        account_pending_max_bytes=settings.connectivity.account_pending_max_bytes,
        batch_max_bytes=settings.connectivity.batch_max_bytes,
        dedup_horizon_seconds=settings.connectivity.dedup_horizon_seconds,
    )
    if input_acceptor is None:
        raise RuntimeError("Canonical Connectivity input commands were not constructed")
    admission = IngressAdmissionReconciler(
        storage.sessions,
        input_acceptor,
        instance_id=settings.service.instance_id or new_object_id("svc"),
        poll_interval_seconds=settings.connectivity.admission_poll_interval_seconds,
        lease_seconds=settings.connectivity.admission_lease_seconds,
        backoff_steps=settings.connectivity.admission_backoff_steps,
        max_backoff_seconds=settings.connectivity.admission_max_backoff_seconds,
        input_max_bytes=settings.connectivity.batch_max_bytes,
    )
    retention = IngressRetentionReconciler(
        storage.sessions,
        poll_interval_seconds=settings.connectivity.retention_poll_interval_seconds,
        batch_size=settings.connectivity.retention_batch_size,
    )
    runtime = ConnectivityDataRuntime(ingress_events=ingress_events)
    background_components = (
        BackgroundTask("ingress admission reconciler", admission.run),
        BackgroundTask("ingress retention reconciler", retention.run),
    )
    return runtime, background_components


__all__ = ["build_connectivity_runtime"]
