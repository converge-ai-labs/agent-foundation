"""FastAPI application factory and process lifespan."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from anyio import fail_after
from fastapi import FastAPI, HTTPException, Request, status
from sqlalchemy import text

from a13n_service import __version__
from a13n_service.agent_configuration.router import router as configuration_router
from a13n_service.agents.router import router as agent_router
from a13n_service.api import api_error_response, install_api_conventions
from a13n_service.assets.router import router as asset_router
from a13n_service.connectivity.accounts.router import router as account_router
from a13n_service.connectivity.accounts.target_router import router as target_router
from a13n_service.connectivity.bots.router import collection_router as bot_collection_router
from a13n_service.connectivity.bots.router import router as bot_router
from a13n_service.connectivity.connections.browser import router as authorization_browser_router
from a13n_service.connectivity.connections.router import router as connection_router
from a13n_service.connectivity.connectors.router import router as connector_router
from a13n_service.connectivity.ingress.data_router import router as ingress_data_router
from a13n_service.connectivity.mcp.router import router as mcp_router
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.environments.router import router as environment_router
from a13n_service.gateway.a2a_router import router as a2a_router
from a13n_service.gateway.router import router as gateway_router
from a13n_service.hooks.router import router as hook_router
from a13n_service.iam.http.auth_router import router as auth_router
from a13n_service.iam.http.image_router import router as image_router
from a13n_service.iam.http.management_router import router as identity_router
from a13n_service.iam.http.profile_router import router as profile_router
from a13n_service.iam.http.recovery_router import router as recovery_router
from a13n_service.interactions.threads import router as thread_router
from a13n_service.lifecycle.router import router as lifecycle_router
from a13n_service.memory.bots.router import router as bot_memory_router
from a13n_service.memory.provider_router import router as memory_provider_router
from a13n_service.memory.router import router as memory_router
from a13n_service.models.providers import ProviderRegistry
from a13n_service.models.router import router as model_router
from a13n_service.openapi import install_openapi
from a13n_service.process.components import Components, snapshot_components
from a13n_service.process.lifecycle import open_process_runtime
from a13n_service.process.roles import owns_connectivity_data, owns_control
from a13n_service.process.runtime import ProcessStatus
from a13n_service.provider_plugins import ProviderCatalogs, load_provider_catalogs
from a13n_service.request_runtime import get_process_runtime
from a13n_service.settings import Settings, get_settings
from a13n_service.skills.router import router as skill_router
from a13n_service.storage import short_session
from a13n_service.trace_query.provider import TraceQueryProviderRegistry
from a13n_service.trace_query.router import router as trace_query_router
from a13n_service.web.router import router as web_router

logger = logging.getLogger("a13n_service.app")

_API_METHODS = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]


def _lifespan(
    settings: Settings,
    components: Components,
    process_status: ProcessStatus,
    trace_query_provider_registry: TraceQueryProviderRegistry,
    provider_catalogs: ProviderCatalogs,
):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_process_runtime(
            settings,
            components,
            process_status,
            trace_query_provider_registry=trace_query_provider_registry,
            model_provider_registry=ProviderRegistry(provider_catalogs.model),
            provider_catalogs=provider_catalogs,
            model_endpoint_policy=EndpointPolicy.from_operator_allowlist(
                private_domains=settings.models.private_endpoint_domains,
                private_cidrs=settings.models.private_endpoint_cidrs,
            ),
        ) as runtime:
            app.state.runtime = runtime
            yield

    return lifespan


def create_app(settings: Settings | None = None, *, components: Components | None = None) -> FastAPI:
    """Create an application without opening external resources."""

    resolved_settings = settings or get_settings()
    provider_catalogs = load_provider_catalogs(resolved_settings.provider_plugins.enabled)
    logger.info(
        "provider_plugins_loaded",
        extra={
            "event": "provider_plugins_loaded",
            "plugins": [
                {
                    "entry_point": plugin.entry_point,
                    "distribution": plugin.distribution_name,
                    "version": plugin.distribution_version,
                }
                for plugin in provider_catalogs.plugins
            ],
            "provider_types": {
                "environment": [provider.key for provider in provider_catalogs.environment],
                "model": [provider.type for provider in provider_catalogs.model],
                "connector": [provider.type for provider in provider_catalogs.connector],
                "web": [provider.type for provider in provider_catalogs.web],
            },
        },
    )
    resolved_components = snapshot_components(
        resolved_settings,
        components or Components(),
    )
    trace_query_provider_registry = resolved_components.trace_query_provider_registry or TraceQueryProviderRegistry()
    for key in ("langfuse", "logfire"):
        if key in trace_query_provider_registry.keys():
            raise ValueError(f"Trace Query provider key is already registered: {key}")
    resolved_settings.validate_trace_query_configuration(
        registered_provider_keys=(*trace_query_provider_registry.keys(), "langfuse", "logfire")
    )
    process_status = ProcessStatus()
    serves_control_plane = owns_control(resolved_settings.service.role)
    app = FastAPI(
        title="a13n Service",
        version=__version__,
        lifespan=_lifespan(
            resolved_settings,
            resolved_components,
            process_status,
            trace_query_provider_registry.copy(),
            provider_catalogs,
        ),
        openapi_url="/api/openapi.json" if serves_control_plane else None,
        docs_url="/api/docs" if serves_control_plane else None,
        redoc_url="/api/redoc" if serves_control_plane else None,
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect" if serves_control_plane else None,
    )
    app.state.settings = resolved_settings

    @app.middleware("http")
    async def reject_during_drain(request: Request, call_next):
        if process_status.draining and request.url.path not in {"/healthz", "/readyz"}:
            return api_error_response(
                request,
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "service_unavailable",
                "The service is temporarily unavailable.",
            )
        return await call_next(request)

    # Register identity last so it also wraps early middleware responses.
    install_api_conventions(app)

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": resolved_settings.service.role.value}

    @app.get("/readyz", include_in_schema=False)
    async def readiness(request: Request) -> dict[str, str]:
        runtime = get_process_runtime(request)
        if not process_status.startup_complete or process_status.draining or runtime is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service not ready",
            )
        storage = runtime.shared.storage
        try:
            with fail_after(resolved_settings.database.readiness_timeout_seconds):
                async with short_session(storage.sessions) as session:
                    await session.execute(text("SELECT 1"))
                await storage.redis.ping()
        except Exception as exc:
            logger.warning(
                "storage_readiness_failed",
                extra={"event": "storage_readiness_failed", "role": resolved_settings.service.role.value},
                exc_info=True,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="required storage unavailable",
            ) from exc
        return {"status": "ready", "role": resolved_settings.service.role.value}

    if owns_connectivity_data(resolved_settings.service.role):
        app.include_router(ingress_data_router)

    if serves_control_plane:
        app.include_router(auth_router)
        app.include_router(identity_router)
        app.include_router(profile_router)
        app.include_router(recovery_router)
        app.include_router(image_router)
        app.include_router(agent_router)
        app.include_router(configuration_router)
        app.include_router(environment_router)
        app.include_router(thread_router)
        app.include_router(asset_router)
        app.include_router(model_router)
        app.include_router(web_router)
        app.include_router(memory_router)
        app.include_router(bot_memory_router)
        app.include_router(memory_provider_router)
        app.include_router(skill_router)
        app.include_router(trace_query_router)
        # Match /targets before the Account lifecycle /{action} route.
        app.include_router(target_router)
        app.include_router(account_router)
        app.include_router(bot_router)
        app.include_router(bot_collection_router)
        app.include_router(connector_router)
        app.include_router(mcp_router)
        app.include_router(connection_router)
        app.include_router(authorization_browser_router)
        app.include_router(hook_router)
        app.include_router(lifecycle_router)
        app.include_router(gateway_router)
        if resolved_settings.gateway.a2a_enabled:
            app.include_router(a2a_router)

        @app.api_route("/api", methods=_API_METHODS, include_in_schema=False)
        async def unknown_api_root() -> None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API route not found")

        @app.api_route("/api/{api_path:path}", methods=_API_METHODS, include_in_schema=False)
        async def unknown_api_path(api_path: str) -> None:
            del api_path
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API route not found")

    install_openapi(app, session_cookie_name=resolved_settings.iam.session_cookie_name)
    return app


__all__ = ["Components", "create_app"]
