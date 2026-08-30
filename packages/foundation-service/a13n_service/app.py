"""FastAPI application factory and process lifespan."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx2
from anyio import fail_after
from fastapi import FastAPI, HTTPException, Request, status
from sqlalchemy import text

from a13n_service.api import install_api_conventions
from a13n_service.connectors import (
    ConnectionService,
    ConnectorProviderCatalog,
    ConnectorService,
    DatabaseConnectorSecretStore,
    build_connector_provider_catalog,
)
from a13n_service.connectors.router import router as connector_router
from a13n_service.iam import RequestAuthenticator
from a13n_service.model_management.connection_test import NativeModelConnectionTester
from a13n_service.model_management.endpoint_policy import EndpointPolicy
from a13n_service.model_management.providers import built_in_provider_registry
from a13n_service.model_management.router import router as model_management_router
from a13n_service.model_management.runtime import (
    AcceptedModelSelector,
    NativeModelFactory,
    RuntimeSecretValueResolver,
)
from a13n_service.model_management.secrets import DatabaseSecretValueResolver
from a13n_service.model_management.service import (
    CandidateConnectionTester,
    ModelConfigService,
)
from a13n_service.settings import ServiceRole, ServiceSettings, get_settings
from a13n_service.storage import StorageResources, open_storage, short_session
from a13n_service.web import mount_web_application

logger = logging.getLogger("a13n_service.app")

_API_METHODS = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
_CONTROL_PLANE_ROLES = {ServiceRole.all, ServiceRole.control}


@dataclass(frozen=True, slots=True)
class ServiceComponents:
    request_authenticator: RequestAuthenticator | None = None
    model_connection_tester: CandidateConnectionTester | None = None
    model_secret_resolver: RuntimeSecretValueResolver | None = None
    connector_provider_catalog: ConnectorProviderCatalog | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings: ServiceSettings = app.state.settings
    component_catalog = app.state.components.connector_provider_catalog
    app.state.connector_providers = (
        component_catalog
        if component_catalog is not None
        else build_connector_provider_catalog(settings.connector_providers)
    )

    async def validate_model_request(request: httpx2.Request) -> None:
        await app.state.model_endpoint_policy.validate(str(request.url), resolve_dns=True)

    async with open_storage(settings.storage_settings()) as storage:
        app.state.storage = storage
        # Keep these names for service code that only needs relational access.
        app.state.db_engine = storage.engine
        app.state.db_session_factory = storage.sessions
        secret_protector = settings.secret_protector()
        app.state.connector_secret_store = DatabaseConnectorSecretStore(storage.sessions, secret_protector)
        app.state.connector_service = ConnectorService(storage.sessions, app.state.connector_providers)
        app.state.connection_service = ConnectionService(
            storage.sessions,
            app.state.connector_providers,
            app.state.connector_secret_store,
        )
        async with httpx2.AsyncClient(
            follow_redirects=False,
            event_hooks={"request": [validate_model_request]},
        ) as model_http_client:
            secret_resolver = app.state.components.model_secret_resolver
            if secret_resolver is None:
                secret_resolver = DatabaseSecretValueResolver(storage.sessions, secret_protector)
            connection_tester = app.state.components.model_connection_tester
            if connection_tester is None and secret_resolver is not None:
                connection_tester = NativeModelConnectionTester(
                    secret_resolver=secret_resolver,
                    endpoint_policy=app.state.model_endpoint_policy,
                    http_client=model_http_client,
                )
            app.state.model_secret_resolver = secret_resolver
            if settings.role in _CONTROL_PLANE_ROLES:
                app.state.accepted_model_selector = AcceptedModelSelector(
                    storage.sessions,
                    app.state.model_provider_registry,
                    app.state.model_endpoint_policy,
                )
                app.state.model_config_service = ModelConfigService(
                    storage.sessions,
                    app.state.model_provider_registry,
                    app.state.model_endpoint_policy,
                    resolve_dns_on_save=settings.model_resolve_dns_on_save,
                    connection_tester=connection_tester,
                    connection_test_timeout_seconds=settings.model_connection_test_timeout_seconds,
                )
            if settings.role in {ServiceRole.all, ServiceRole.worker}:
                app.state.native_model_factory = NativeModelFactory(model_http_client)
            logger.info(
                "service_started",
                extra={
                    "event": "service_started",
                    "service": settings.service_name,
                    "role": settings.role.value,
                    "build_version": settings.build_version,
                },
            )
            try:
                yield
            finally:
                logger.info(
                    "service_stopped",
                    extra={"event": "service_stopped", "service": settings.service_name, "role": settings.role.value},
                )


def create_app(settings: ServiceSettings | None = None, *, components: ServiceComponents | None = None) -> FastAPI:
    """Create an application without opening external resources."""

    resolved_settings = settings or get_settings()
    serves_control_plane = resolved_settings.role in _CONTROL_PLANE_ROLES
    app = FastAPI(
        title="Agent Foundation Service",
        version=resolved_settings.build_version,
        lifespan=_lifespan,
        openapi_url="/api/openapi.json" if serves_control_plane else None,
        docs_url="/api/docs" if serves_control_plane else None,
        redoc_url="/api/redoc" if serves_control_plane else None,
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect" if serves_control_plane else None,
    )
    install_api_conventions(app)
    app.state.settings = resolved_settings
    app.state.components = components or ServiceComponents()
    app.state.request_authenticator = app.state.components.request_authenticator
    app.state.model_provider_registry = built_in_provider_registry()
    app.state.model_endpoint_policy = EndpointPolicy.from_operator_allowlist(
        private_domains=resolved_settings.model_private_endpoint_domains,
        private_cidrs=resolved_settings.model_private_endpoint_cidrs,
    )

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": resolved_settings.role.value}

    @app.get("/readyz", include_in_schema=False)
    async def readiness(request: Request) -> dict[str, str]:
        storage: StorageResources = request.app.state.storage
        try:
            with fail_after(resolved_settings.database_readiness_timeout_seconds):
                async with short_session(storage.sessions) as session:
                    await session.execute(text("SELECT 1"))
                await storage.redis.ping()
        except Exception as exc:
            logger.warning(
                "storage_readiness_failed",
                extra={"event": "storage_readiness_failed", "role": resolved_settings.role.value},
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="required storage unavailable",
            ) from exc
        return {"status": "ready", "role": resolved_settings.role.value}

    if serves_control_plane:
        app.include_router(connector_router)
        app.include_router(model_management_router)

        @app.api_route("/api", methods=_API_METHODS, include_in_schema=False)
        async def unknown_api_root() -> None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API route not found")

        @app.api_route("/api/{api_path:path}", methods=_API_METHODS, include_in_schema=False)
        async def unknown_api_path(api_path: str) -> None:
            del api_path
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API route not found")

        if resolved_settings.web_dist_dir is not None:
            mount_web_application(app, resolved_settings.web_dist_dir)

    return app
