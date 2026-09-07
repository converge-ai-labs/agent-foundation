"""Connectivity composition for Foundation Service process roles."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass

import httpx2

from a13n_service.connectivity.accounts.service import AccountService
from a13n_service.connectivity.accounts.target_service import AccountTargetService
from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.connectors.providers import built_in_connector_provider_registry
from a13n_service.connectivity.connectors.reconciler import ConnectorReconciler
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.connectors.service import ConnectorProviderService
from a13n_service.connectivity.http import cookie_free_jar
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.admission_domain import InputAcceptor
from a13n_service.connectivity.ingress.reconciler import IngressAdmissionReconciler
from a13n_service.connectivity.ingress.retention import IngressRetentionReconciler
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
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.ids import new_object_id
from a13n_service.process.background import BackgroundTask
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
    input_acceptor: InputAcceptor | None,
    control_plane: bool,
    data_plane: bool,
) -> tuple[ConnectivityRuntime | None, ConnectivitySelectionResolver | None, tuple[BackgroundTask, ...]]:
    """Construct only the Connectivity capabilities owned by this role."""

    if not control_plane and not data_plane:
        return None, None, ()
    if ingress_adapters is None:
        raise RuntimeError("Connectivity ingress adapters were not prepared")
    control, selection_resolver, control_components = (
        await _build_control_runtime(
            settings,
            storage,
            ingress_adapters,
            connector_providers,
            secret_protector,
            stack,
        )
        if control_plane
        else (None, None, ())
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
        selection_resolver,
        (*control_components, *data_components),
    )


async def _build_control_runtime(
    settings: Settings,
    storage: StorageResources,
    ingress_adapters: AdapterRegistry[IngressAdapter],
    connector_providers: ConnectorProviderRegistry | None,
    secret_protector: SecretProtector,
    stack: AsyncExitStack,
) -> tuple[ConnectivityControlRuntime, ConnectivitySelectionResolver, tuple[BackgroundTask, ...]]:
    public_origin = settings.validated_connectivity_public_origin() if settings.connectivity_public_origin else None
    endpoint_policy = settings.connectivity_endpoint_policy()
    if connector_providers is None:
        connector_http_client = await stack.enter_async_context(
            httpx2.AsyncClient(
                cookies=cookie_free_jar(),
                follow_redirects=False,
                timeout=connectivity_http_timeout(settings),
            )
        )
        connector_providers = built_in_connector_provider_registry(
            connector_http_client,
            endpoint_policy,
            response_max_bytes=settings.connectivity_response_max_bytes,
            timeout_seconds=settings.connectivity_total_timeout_seconds,
        )
    selection_resolver = ConnectivitySelectionResolver(storage.sessions)
    connector = _build_connector_control(
        settings,
        storage,
        connector_providers,
        secret_protector,
        public_origin,
    )
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
        public_origin,
        mcp_http_client,
    )
    runtime = ConnectivityControlRuntime(
        public_origin=public_origin,
        accounts=AccountService(
            storage.sessions,
            ingress_adapters,
            secret_protector,
            batch_max_events=settings.connectivity_batch_max_events,
            batch_max_wait_seconds=settings.connectivity_batch_max_wait_seconds,
        ),
        targets=AccountTargetService(
            storage.sessions,
            ingress_adapters,
            batch_max_events=settings.connectivity_batch_max_events,
            batch_max_wait_seconds=settings.connectivity_batch_max_wait_seconds,
        ),
        connector_providers=connector.service,
        connector_connections=connector.connections,
        mcp_connections=mcp.connections,
        mcp_oauth=mcp.oauth,
    )
    background_components = (
        BackgroundTask("connector reconciler", connector.reconciler.run),
        BackgroundTask("MCP reconciler", mcp.reconciler.run),
    )
    return runtime, selection_resolver, background_components


def _build_connector_control(
    settings: Settings,
    storage: StorageResources,
    connector_providers: ConnectorProviderRegistry,
    secret_protector: SecretProtector,
    public_origin: str | None,
) -> _ConnectorControl:
    service = ConnectorProviderService(storage.sessions, connector_providers, secret_protector)
    correlation_secret = settings.connectivity_setup_correlation_secret
    connections = ConnectorConnectionService(
        storage.sessions,
        connector_providers,
        secret_protector,
        correlation_secret=(correlation_secret.get_secret_value().encode() if correlation_secret is not None else None),
        public_origin=public_origin,
        setup_ttl_seconds=settings.connectivity_oauth_setup_ttl_seconds,
    )
    instance_id = settings.service_instance_id or new_object_id("svc")
    reconciler = ConnectorReconciler(
        storage.sessions,
        connector_providers,
        connections.setup_coordinator,
        instance_id=instance_id,
        poll_interval_seconds=settings.connectivity_connector_reconcile_poll_interval_seconds,
        lease_seconds=settings.connectivity_connector_reconcile_lease_seconds,
    )
    return _ConnectorControl(service=service, connections=connections, reconciler=reconciler)


def _build_mcp_control(
    settings: Settings,
    storage: StorageResources,
    endpoint_policy: EndpointPolicy,
    secret_protector: SecretProtector,
    public_origin: str | None,
    http_client: httpx2.AsyncClient,
) -> _MCPControl:
    instance_id = settings.service_instance_id or new_object_id("svc")
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
        public_origin=public_origin,
        client_name=settings.connectivity_oauth_client_name,
        instance_id=instance_id,
        setup_ttl_seconds=settings.connectivity_oauth_setup_ttl_seconds,
        claim_lease_seconds=settings.connectivity_connector_reconcile_lease_seconds,
    )
    reconciler = MCPReconciler(
        storage.sessions,
        poll_interval_seconds=settings.connectivity_connector_reconcile_poll_interval_seconds,
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
        request_max_bytes=settings.connectivity_provider_request_max_bytes,
        workspace_pending_max_count=settings.connectivity_workspace_pending_max_count,
        workspace_pending_max_bytes=settings.connectivity_workspace_pending_max_bytes,
        account_pending_max_count=settings.connectivity_account_pending_max_count,
        account_pending_max_bytes=settings.connectivity_account_pending_max_bytes,
        batch_max_bytes=settings.connectivity_batch_max_bytes,
        dedup_horizon_seconds=settings.connectivity_dedup_horizon_seconds,
    )
    if input_acceptor is None:
        raise RuntimeError("Canonical Connectivity input commands were not constructed")
    admission = IngressAdmissionReconciler(
        storage.sessions,
        input_acceptor,
        instance_id=settings.service_instance_id or new_object_id("svc"),
        poll_interval_seconds=settings.connectivity_admission_poll_interval_seconds,
        lease_seconds=settings.connectivity_admission_lease_seconds,
        backoff_steps=settings.connectivity_admission_backoff_steps,
        max_backoff_seconds=settings.connectivity_admission_max_backoff_seconds,
        input_max_bytes=settings.connectivity_batch_max_bytes,
    )
    retention = IngressRetentionReconciler(
        storage.sessions,
        poll_interval_seconds=settings.connectivity_retention_poll_interval_seconds,
        batch_size=settings.connectivity_retention_batch_size,
    )
    runtime = ConnectivityDataRuntime(ingress_events=ingress_events)
    background_components = (
        BackgroundTask("ingress admission reconciler", admission.run),
        BackgroundTask("ingress retention reconciler", retention.run),
    )
    return runtime, background_components


__all__ = ["build_connectivity_runtime"]
