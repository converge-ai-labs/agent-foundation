"""Connectivity lifespan composition for Foundation Service roles."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack

import httpx2
from fastapi import FastAPI

from a13n_service.connectivity.connectors.catalog import ConnectorCatalogService
from a13n_service.connectivity.connectors.catalog_objects import ConnectorCatalogObjectStore
from a13n_service.connectivity.connectors.connections import ConnectorConnectionService
from a13n_service.connectivity.connectors.providers import built_in_connector_adapter_registry
from a13n_service.connectivity.connectors.reconciler import ConnectorReconciler
from a13n_service.connectivity.connectors.service import ConnectorService
from a13n_service.connectivity.ingress.admission import IngressEventService
from a13n_service.connectivity.ingress.admission_domain import UnavailableFoundationInputAcceptor
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
from a13n_service.connectivity.selection_resolution import ConnectivitySelectionResolver
from a13n_service.ids import new_object_id
from a13n_service.secrets import InternalSecretService, SecretProtector
from a13n_service.settings import ServiceSettings
from a13n_service.storage import StorageResources

logger = logging.getLogger("a13n_service.connectivity.lifespan")

BackgroundComponent = tuple[str, Callable[[], Awaitable[None]]]


async def install_connectivity_lifespan(
    app: FastAPI,
    settings: ServiceSettings,
    storage: StorageResources,
    stack: AsyncExitStack,
    secret_protector: SecretProtector,
    *,
    control_plane: bool,
    data_plane: bool,
) -> tuple[BackgroundComponent, ...]:
    """Install the Connectivity services owned by this process role."""

    if not control_plane and not data_plane:
        return ()

    app.state.internal_secret_service = InternalSecretService(
        storage.sessions,
        secret_protector,
    )
    components: list[BackgroundComponent] = []
    if control_plane:
        components.extend(await _install_control_plane(app, settings, storage, stack))
    if data_plane:
        components.extend(_install_data_plane(app, settings, storage))
    return tuple(components)


async def _install_control_plane(
    app: FastAPI,
    settings: ServiceSettings,
    storage: StorageResources,
    stack: AsyncExitStack,
) -> tuple[BackgroundComponent, ...]:
    app.state.connectivity_public_origin = settings.validated_connectivity_public_origin()
    connector_http_client = await stack.enter_async_context(
        httpx2.AsyncClient(
            follow_redirects=False,
            timeout=settings.connectivity_total_timeout_seconds,
        )
    )
    if app.state.uses_builtin_connector_adapters:
        app.state.connector_adapter_registry = built_in_connector_adapter_registry(
            connector_http_client,
            app.state.connectivity_endpoint_policy,
            response_max_bytes=settings.connectivity_response_max_bytes,
        )
    mcp_http_client = await stack.enter_async_context(
        httpx2.AsyncClient(
            follow_redirects=False,
            timeout=settings.connectivity_total_timeout_seconds,
        )
    )

    app.state.connectivity_selection_resolver = ConnectivitySelectionResolver(
        storage.sessions,
        storage.objects,
    )
    connector_reconciler = _install_connector_services(app, settings, storage)
    mcp_reconciler = _install_mcp_services(app, settings, storage, mcp_http_client)
    catalog_retention_reconciler = CatalogRetentionReconciler(
        storage.sessions,
        storage.objects,
        instance_id=settings.service_instance_id or new_object_id("svc"),
        poll_interval_seconds=settings.connectivity_retention_poll_interval_seconds,
        lease_seconds=settings.connectivity_retention_lease_seconds,
        object_grace_seconds=settings.connectivity_object_cleanup_grace_seconds,
        batch_size=settings.connectivity_retention_batch_size,
    )
    app.state.catalog_retention_reconciler = catalog_retention_reconciler
    return (
        ("catalog retention reconciler", catalog_retention_reconciler.run),
        ("Connector reconciler", connector_reconciler.run),
        ("MCP reconciler", mcp_reconciler.run),
    )


def _install_connector_services(
    app: FastAPI,
    settings: ServiceSettings,
    storage: StorageResources,
) -> ConnectorReconciler:
    app.state.ingress_service = IngressService(
        storage.sessions,
        app.state.ingress_adapter_registry,
        app.state.internal_secret_service,
    )
    app.state.route_service = RouteService(
        storage.sessions,
        app.state.ingress_adapter_registry,
        batch_max_events=settings.connectivity_batch_max_events,
        batch_max_wait_seconds=settings.connectivity_batch_max_wait_seconds,
    )
    app.state.connector_service = ConnectorService(
        storage.sessions,
        app.state.connector_adapter_registry,
        app.state.internal_secret_service,
    )
    correlation_secret = settings.connectivity_setup_correlation_secret
    app.state.connector_connection_service = ConnectorConnectionService(
        storage.sessions,
        app.state.connector_adapter_registry,
        app.state.internal_secret_service,
        correlation_secret=(correlation_secret.get_secret_value().encode() if correlation_secret is not None else None),
        public_origin=app.state.connectivity_public_origin,
        setup_ttl_seconds=settings.connectivity_oauth_setup_ttl_seconds,
    )
    instance_id = settings.service_instance_id or new_object_id("svc")
    catalog_service = ConnectorCatalogService(
        storage.sessions,
        app.state.connector_adapter_registry,
        app.state.internal_secret_service,
        ConnectorCatalogObjectStore(storage.objects),
        instance_id=instance_id,
        lease_seconds=settings.connectivity_connector_reconcile_lease_seconds,
        retention_seconds=settings.connectivity_catalog_retention_seconds,
    )
    app.state.connector_catalog_service = catalog_service
    reconciler = ConnectorReconciler(
        storage.sessions,
        app.state.connector_adapter_registry,
        app.state.connector_connection_service.setup_coordinator,
        app.state.connector_connection_service,
        catalog_service,
        instance_id=instance_id,
        poll_interval_seconds=settings.connectivity_connector_reconcile_poll_interval_seconds,
        lease_seconds=settings.connectivity_connector_reconcile_lease_seconds,
    )
    app.state.connector_reconciler = reconciler
    return reconciler


def _install_mcp_services(
    app: FastAPI,
    settings: ServiceSettings,
    storage: StorageResources,
    http_client: httpx2.AsyncClient,
) -> MCPReconciler:
    instance_id = settings.service_instance_id or new_object_id("svc")
    catalog_service = MCPCatalogService(
        storage.sessions,
        MCPProtocolClient(
            http_client,
            app.state.connectivity_endpoint_policy,
            response_max_bytes=settings.connectivity_catalog_max_bytes,
            max_redirects=settings.connectivity_max_redirects,
        ),
        app.state.internal_secret_service,
        MCPCatalogObjectStore(storage.objects),
        instance_id=instance_id,
        lease_seconds=settings.connectivity_connector_reconcile_lease_seconds,
        retention_seconds=settings.connectivity_catalog_retention_seconds,
    )
    app.state.mcp_catalog_service = catalog_service
    oauth_client = MCPOAuthClient(
        http_client,
        app.state.connectivity_endpoint_policy,
        response_max_bytes=settings.connectivity_response_max_bytes,
        max_redirects=settings.connectivity_max_redirects,
    )
    app.state.mcp_oauth_client = oauth_client
    app.state.mcp_connection_service = MCPConnectionService(
        storage.sessions,
        app.state.connectivity_endpoint_policy,
        app.state.internal_secret_service,
        catalog_service,
        registration_cleaner=oauth_client,
    )
    app.state.mcp_oauth_service = MCPOAuthService(
        storage.sessions,
        oauth_client,
        app.state.internal_secret_service,
        catalog_service,
        public_origin=app.state.connectivity_public_origin,
        client_name=settings.connectivity_oauth_client_name,
        instance_id=instance_id,
        setup_ttl_seconds=settings.connectivity_oauth_setup_ttl_seconds,
        claim_lease_seconds=settings.connectivity_connector_reconcile_lease_seconds,
    )
    reconciler = MCPReconciler(
        storage.sessions,
        app.state.mcp_connection_service,
        app.state.mcp_oauth_service,
        catalog_service,
        poll_interval_seconds=settings.connectivity_connector_reconcile_poll_interval_seconds,
        refresh_skew_seconds=settings.connectivity_provider_token_expiry_skew_seconds,
    )
    app.state.mcp_reconciler = reconciler
    return reconciler


def _install_data_plane(
    app: FastAPI,
    settings: ServiceSettings,
    storage: StorageResources,
) -> tuple[BackgroundComponent, ...]:
    app.state.ingress_event_service = IngressEventService(
        storage.sessions,
        app.state.ingress_adapter_registry,
        app.state.internal_secret_service,
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
    input_acceptor = app.state.components.foundation_input_acceptor
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
    admission_reconciler = IngressAdmissionReconciler(
        storage.sessions,
        input_acceptor,
        instance_id=settings.service_instance_id or new_object_id("svc"),
        poll_interval_seconds=settings.connectivity_admission_poll_interval_seconds,
        lease_seconds=settings.connectivity_admission_lease_seconds,
        max_attempts=settings.connectivity_admission_max_attempts,
        max_backoff_seconds=settings.connectivity_admission_max_backoff_seconds,
    )
    app.state.ingress_admission_reconciler = admission_reconciler
    retention_reconciler = IngressRetentionReconciler(
        storage.sessions,
        storage.objects,
        poll_interval_seconds=settings.connectivity_retention_poll_interval_seconds,
        object_grace_seconds=settings.connectivity_object_cleanup_grace_seconds,
        batch_size=settings.connectivity_retention_batch_size,
    )
    app.state.ingress_retention_reconciler = retention_reconciler
    return (
        ("Ingress admission reconciler", admission_reconciler.run),
        ("Ingress retention reconciler", retention_reconciler.run),
    )
