"""FastAPI application factory and process lifespan."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from anyio import fail_after
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from a13n_service.agents.router import router as agent_router
from a13n_service.api import install_api_conventions
from a13n_service.assets.router import router as asset_router
from a13n_service.connectivity.connectors.router import router as connector_router
from a13n_service.connectivity.ingress.data_router import router as ingress_data_router
from a13n_service.connectivity.ingress.router import router as ingress_router
from a13n_service.connectivity.mcp.router import router as mcp_router
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.environments.router import router as environment_router
from a13n_service.models.providers import built_in_provider_registry
from a13n_service.models.router import router as model_router
from a13n_service.plugins.router import router as plugin_router
from a13n_service.process.components import ServiceComponents, snapshot_service_components
from a13n_service.process.lifecycle import open_service_runtime
from a13n_service.process.roles import owns_connectivity_data, owns_control
from a13n_service.process.runtime import ProcessStatus, get_service_runtime
from a13n_service.settings import ServiceSettings, get_settings
from a13n_service.skills.router import router as skill_router
from a13n_service.storage import short_session
from a13n_service.trace_query.provider import TraceQueryProviderRegistry
from a13n_service.trace_query.router import router as trace_query_router
from a13n_service.web import mount_web_application

logger = logging.getLogger("a13n_service.app")

_API_METHODS = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]


def _lifespan(
    settings: ServiceSettings,
    components: ServiceComponents,
    process_status: ProcessStatus,
    trace_query_provider_registry: TraceQueryProviderRegistry,
):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_service_runtime(
            settings,
            components,
            process_status,
            trace_query_provider_registry=trace_query_provider_registry,
            model_provider_registry=built_in_provider_registry(),
            model_endpoint_policy=EndpointPolicy.from_operator_allowlist(
                private_domains=settings.model_private_endpoint_domains,
                private_cidrs=settings.model_private_endpoint_cidrs,
            ),
        ) as runtime:
            app.state.runtime = runtime
            yield

    return lifespan


def create_app(settings: ServiceSettings | None = None, *, components: ServiceComponents | None = None) -> FastAPI:
    """Create an application without opening external resources."""

    resolved_settings = settings or get_settings()
    resolved_components = snapshot_service_components(
        resolved_settings,
        components or ServiceComponents(),
    )
    trace_query_provider_registry = resolved_components.trace_query_provider_registry or TraceQueryProviderRegistry()
    if "langfuse" in trace_query_provider_registry.keys():
        raise ValueError("Trace Query provider key is already registered: langfuse")
    resolved_settings.validate_trace_query_configuration(
        registered_provider_keys=(*trace_query_provider_registry.keys(), "langfuse")
    )
    process_status = ProcessStatus()
    serves_control_plane = owns_control(resolved_settings.role)
    app = FastAPI(
        title="Agent Foundation Service",
        version=resolved_settings.build_version,
        lifespan=_lifespan(
            resolved_settings,
            resolved_components,
            process_status,
            trace_query_provider_registry.copy(),
        ),
        openapi_url="/api/openapi.json" if serves_control_plane else None,
        docs_url="/api/docs" if serves_control_plane else None,
        redoc_url="/api/redoc" if serves_control_plane else None,
        swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect" if serves_control_plane else None,
    )
    install_api_conventions(app)

    @app.middleware("http")
    async def reject_during_drain(request: Request, call_next):
        if process_status.draining and request.url.path not in {"/healthz", "/readyz"}:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "service draining"},
            )
        return await call_next(request)

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": resolved_settings.role.value}

    @app.get("/readyz", include_in_schema=False)
    async def readiness(request: Request) -> dict[str, str]:
        runtime = get_service_runtime(request)
        if not process_status.startup_complete or process_status.draining or runtime is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="service not ready",
            )
        storage = runtime.shared.storage
        on_demand_runtime = runtime.worker.on_demand_plugins if runtime.worker is not None else None
        if on_demand_runtime is not None and not on_demand_runtime.ready:
            logger.warning(
                "plugin_runtime_readiness_failed",
                extra={"event": "plugin_runtime_readiness_failed", "role": resolved_settings.role.value},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="plugin runtime unavailable",
            )
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

    if owns_connectivity_data(resolved_settings.role):
        app.include_router(ingress_data_router)

    if serves_control_plane:
        app.include_router(agent_router)
        app.include_router(environment_router)
        app.include_router(asset_router)
        app.include_router(model_router)
        app.include_router(plugin_router)
        app.include_router(skill_router)
        app.include_router(trace_query_router)
        app.include_router(ingress_router)
        app.include_router(connector_router)
        app.include_router(mcp_router)

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


__all__ = ["ServiceComponents", "create_app"]
