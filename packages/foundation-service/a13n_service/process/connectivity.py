"""Connectivity composition for Foundation Service process roles."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass

import httpx2

from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.connectors.catalog import ConnectorCatalogService
from a13n_service.connectivity.connectors.catalog_objects import ConnectorCatalogObjectStore
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.connectors.providers import built_in_connector_provider_registry
from a13n_service.connectivity.connectors.reconciler import ConnectorReconciler
from a13n_service.connectivity.connectors.registry import ConnectorProviderRegistry
from a13n_service.connectivity.connectors.service import ConnectorProviderService
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.admission_domain import (
    FoundationInputAcceptor,
    UnavailableFoundationInputAcceptor,
)
from a13n_service.connectivity.ingress.raw_objects import IngressRawObjectStore
from a13n_service.connectivity.ingress.reconciler import IngressAdmissionReconciler
from a13n_service.connectivity.ingress.retention import IngressRetentionReconciler
from a13n_service.connectivity.ingress.routes import RouteService
from a13n_service.connectivity.ingress.service import IngressService
from a13n_service.connectivity.mcp.catalog_objects import MCPCatalogObjectStore
from a13n_service.connectivity.mcp.catalog_service import MCPCatalogService
from a13n_service.connectivity.mcp.oauth_client import MCPOAuthClient
from a13n_service.connectivity.mcp.oauth_service import MCPOAuthService
from a13n_service.connectivity.mcp.protocol import MCPProtocolClient
from a13n_service.connectivity.mcp.reconciler import MCPReconciler
from a13n_service.connectivity.mcp.service import MCPConnectionService
from a13n_service.connectivity.retention import CatalogRetentionReconciler
from a13n_service.connectivity.runtime import (
    ConnectivityControlRuntime,
    ConnectivityDataRuntime,
    ConnectivityRuntime,
)
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.ids import new_object_id
from a13n_service.process.background import BackgroundTask
from a13n_service.secrets import InternalSecretService, SecretProtector
from a13n_service.settings import ServiceSettings
from a13n_service.storage import StorageResources

logger = logging.getLogger("a13n_service.process.connectivity")


@dataclass(frozen=True, slots=True)
class _ConnectorControl:
    service: ConnectorProviderService
    connections: ConnectorConnectionService
    catalog: ConnectorCatalogService
    reconciler: ConnectorReconciler


@dataclass(frozen=True, slots=True)
class _MCPControl:
    catalog: MCPCatalogService
    oauth_client: MCPOAuthClient
    connections: MCPConnectionService
    oauth: MCPOAuthService
    reconciler: MCPReconciler


async def build_connectivity_runtime(
    settings: ServiceSettings,
    storage: StorageResources,
    secret_protector: SecretProtector,
    stack: AsyncExitStack,
    *,
    ingress_adapters: AdapterRegistry[IngressAdapter] | None,
    connector_providers: ConnectorProviderRegistry | None,
    input_acceptor: FoundationInputAcceptor | None,
    control_plane: bool,
    data_plane: bool,
) -> tuple[ConnectivityRuntime | None, ConnectivitySelectionResolver | None, tuple[BackgroundTask, ...]]:
    """Construct only the Connectivity capabilities owned by this role."""

    if not control_plane and not data_plane:
        return None, None, ()
    if ingress_adapters is None:
        raise RuntimeError("Connectivity ingress adapters were not prepared")
    internal_secrets = InternalSecretService(storage.sessions, secret_protector)
    control, selection_resolver, control_components = (
        await _build_control_runtime(
            settings,
            storage,
            ingress_adapters,
            connector_providers,
            internal_secrets,
            stack,
        )
        if control_plane
        else (None, None, ())
    )
    data, data_components = (
        _build_data_runtime(settings, input_acceptor, storage, ingress_adapters, internal_secrets)
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
    settings: ServiceSettings,
    storage: StorageResources,
    ingress_adapters: AdapterRegistry[IngressAdapter],
    connector_providers: ConnectorProviderRegistry | None,
    internal_secrets: InternalSecretService,
    stack: AsyncExitStack,
) -> tuple[ConnectivityControlRuntime, ConnectivitySelectionResolver, tuple[BackgroundTask, ...]]:
    public_origin = settings.validated_connectivity_public_origin()
    endpoint_policy = settings.connectivity_endpoint_policy()
    if connector_providers is None:
        connector_http_client = await stack.enter_async_context(
            httpx2.AsyncClient(
                follow_redirects=False,
                timeout=settings.connectivity_total_timeout_seconds,
            )
        )
        connector_providers = built_in_connector_provider_registry(
            connector_http_client,
            endpoint_policy,
            response_max_bytes=settings.connectivity_response_max_bytes,
        )
    selection_resolver = ConnectivitySelectionResolver(storage.sessions, storage.objects)
    connector = _build_connector_control(
        settings,
        storage,
        connector_providers,
        internal_secrets,
        public_origin,
    )
    mcp_http_client = await stack.enter_async_context(
        httpx2.AsyncClient(
            follow_redirects=False,
            timeout=settings.connectivity_total_timeout_seconds,
        )
    )
    mcp = _build_mcp_control(
        settings,
        storage,
        endpoint_policy,
        internal_secrets,
        public_origin,
        mcp_http_client,
    )
    catalog_retention = CatalogRetentionReconciler(
        storage.sessions,
        storage.objects,
        instance_id=settings.service_instance_id or new_object_id("svc"),
        poll_interval_seconds=settings.connectivity_retention_poll_interval_seconds,
        lease_seconds=settings.connectivity_retention_lease_seconds,
        object_grace_seconds=settings.connectivity_object_cleanup_grace_seconds,
        batch_size=settings.connectivity_retention_batch_size,
    )
    runtime = ConnectivityControlRuntime(
        public_origin=public_origin,
        ingresses=IngressService(storage.sessions, ingress_adapters, internal_secrets),
        routes=RouteService(
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
        BackgroundTask("catalog retention reconciler", catalog_retention.run),
        BackgroundTask("connector reconciler", connector.reconciler.run),
        BackgroundTask("MCP reconciler", mcp.reconciler.run),
    )
    return runtime, selection_resolver, background_components


def _build_connector_control(
    settings: ServiceSettings,
    storage: StorageResources,
    connector_providers: ConnectorProviderRegistry,
    internal_secrets: InternalSecretService,
    public_origin: str,
) -> _ConnectorControl:
    service = ConnectorProviderService(storage.sessions, connector_providers, internal_secrets)
    correlation_secret = settings.connectivity_setup_correlation_secret
    connections = ConnectorConnectionService(
        storage.sessions,
        connector_providers,
        internal_secrets,
        correlation_secret=(correlation_secret.get_secret_value().encode() if correlation_secret is not None else None),
        public_origin=public_origin,
        setup_ttl_seconds=settings.connectivity_oauth_setup_ttl_seconds,
    )
    instance_id = settings.service_instance_id or new_object_id("svc")
    catalog = ConnectorCatalogService(
        storage.sessions,
        connector_providers,
        internal_secrets,
        ConnectorCatalogObjectStore(storage.objects),
        instance_id=instance_id,
        lease_seconds=settings.connectivity_connector_reconcile_lease_seconds,
        retention_seconds=settings.connectivity_catalog_retention_seconds,
    )
    reconciler = ConnectorReconciler(
        storage.sessions,
        connector_providers,
        connections.setup_coordinator,
        connections,
        catalog,
        instance_id=instance_id,
        poll_interval_seconds=settings.connectivity_connector_reconcile_poll_interval_seconds,
        lease_seconds=settings.connectivity_connector_reconcile_lease_seconds,
    )
    return _ConnectorControl(service=service, connections=connections, catalog=catalog, reconciler=reconciler)


def _build_mcp_control(
    settings: ServiceSettings,
    storage: StorageResources,
    endpoint_policy: EndpointPolicy,
    internal_secrets: InternalSecretService,
    public_origin: str,
    http_client: httpx2.AsyncClient,
) -> _MCPControl:
    instance_id = settings.service_instance_id or new_object_id("svc")
    catalog = MCPCatalogService(
        storage.sessions,
        MCPProtocolClient(
            http_client,
            endpoint_policy,
            response_max_bytes=settings.connectivity_catalog_max_bytes,
            max_redirects=settings.connectivity_max_redirects,
        ),
        internal_secrets,
        MCPCatalogObjectStore(storage.objects),
        instance_id=instance_id,
        lease_seconds=settings.connectivity_connector_reconcile_lease_seconds,
        retention_seconds=settings.connectivity_catalog_retention_seconds,
    )
    oauth_client = MCPOAuthClient(
        http_client,
        endpoint_policy,
        response_max_bytes=settings.connectivity_response_max_bytes,
        max_redirects=settings.connectivity_max_redirects,
    )
    connections = MCPConnectionService(
        storage.sessions,
        endpoint_policy,
        internal_secrets,
        catalog,
        registration_cleaner=oauth_client,
    )
    oauth = MCPOAuthService(
        storage.sessions,
        oauth_client,
        internal_secrets,
        catalog,
        public_origin=public_origin,
        client_name=settings.connectivity_oauth_client_name,
        instance_id=instance_id,
        setup_ttl_seconds=settings.connectivity_oauth_setup_ttl_seconds,
        claim_lease_seconds=settings.connectivity_connector_reconcile_lease_seconds,
    )
    reconciler = MCPReconciler(
        storage.sessions,
        connections,
        oauth,
        catalog,
        poll_interval_seconds=settings.connectivity_connector_reconcile_poll_interval_seconds,
        refresh_skew_seconds=settings.connectivity_provider_token_expiry_skew_seconds,
    )
    return _MCPControl(
        catalog=catalog,
        oauth_client=oauth_client,
        connections=connections,
        oauth=oauth,
        reconciler=reconciler,
    )


def _build_data_runtime(
    settings: ServiceSettings,
    input_acceptor: FoundationInputAcceptor | None,
    storage: StorageResources,
    ingress_adapters: AdapterRegistry[IngressAdapter],
    internal_secrets: InternalSecretService,
) -> tuple[ConnectivityDataRuntime, tuple[BackgroundTask, ...]]:
    ingress_events = IngressEventService(
        storage.sessions,
        ingress_adapters,
        internal_secrets,
        IngressRawObjectStore(storage.objects),
        request_max_bytes=settings.connectivity_provider_request_max_bytes,
        raw_retention_seconds=settings.connectivity_protected_raw_retention_seconds,
        workspace_pending_max_count=settings.connectivity_workspace_pending_max_count,
        workspace_pending_max_bytes=settings.connectivity_workspace_pending_max_bytes,
        ingress_pending_max_count=settings.connectivity_ingress_pending_max_count,
        ingress_pending_max_bytes=settings.connectivity_ingress_pending_max_bytes,
        batch_max_bytes=settings.connectivity_batch_max_bytes,
        dedup_horizon_seconds=settings.connectivity_dedup_horizon_seconds,
    )
    if input_acceptor is None:
        logger.warning(
            "connectivity_input_bridge_unavailable",
            extra={
                "event": "connectivity_input_bridge_unavailable",
                "role": settings.role.value,
            },
        )
        input_acceptor = UnavailableFoundationInputAcceptor(
            retry_seconds=settings.connectivity_admission_poll_interval_seconds
        )
    admission = IngressAdmissionReconciler(
        storage.sessions,
        input_acceptor,
        instance_id=settings.service_instance_id or new_object_id("svc"),
        poll_interval_seconds=settings.connectivity_admission_poll_interval_seconds,
        lease_seconds=settings.connectivity_admission_lease_seconds,
        max_attempts=settings.connectivity_admission_max_attempts,
        max_backoff_seconds=settings.connectivity_admission_max_backoff_seconds,
    )
    retention = IngressRetentionReconciler(
        storage.sessions,
        storage.objects,
        poll_interval_seconds=settings.connectivity_retention_poll_interval_seconds,
        object_grace_seconds=settings.connectivity_object_cleanup_grace_seconds,
        batch_size=settings.connectivity_retention_batch_size,
    )
    runtime = ConnectivityDataRuntime(ingress_events=ingress_events)
    background_components = (
        BackgroundTask("ingress admission reconciler", admission.run),
        BackgroundTask("ingress retention reconciler", retention.run),
    )
    return runtime, background_components


__all__ = ["build_connectivity_runtime"]
