"""FastAPI application factory and process lifespan."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from anyio import fail_after
from fastapi import FastAPI, HTTPException, Request, status
from sqlalchemy import text

from a13n_service.connectors import build_connector_provider_catalog
from a13n_service.settings import ServiceRole, ServiceSettings, get_settings
from a13n_service.storage import StorageResources, open_storage, short_session
from a13n_service.web import mount_web_application

logger = logging.getLogger("a13n_service.app")

_API_METHODS = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
_CONTROL_PLANE_ROLES = {ServiceRole.all, ServiceRole.control}


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings: ServiceSettings = app.state.settings
    app.state.connector_providers = build_connector_provider_catalog(settings.connector_providers)
    async with open_storage(settings.storage_settings()) as storage:
        app.state.storage = storage
        # Keep these names for service code that only needs relational access.
        app.state.db_engine = storage.engine
        app.state.db_session_factory = storage.sessions
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


def create_app(settings: ServiceSettings | None = None) -> FastAPI:
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
    app.state.settings = resolved_settings

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
