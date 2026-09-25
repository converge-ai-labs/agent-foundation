"""Assembly: one application per process role, built from one distribution.

`all` serves the API, runs control sweeps and executes runs; `control` omits execution; `worker` executes
runs only and never migrates.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial
from importlib.metadata import version
from typing import Any, get_args

from a13n_harness import HarnessTraceContent
from a13n_harness.plugin_factories import build_harness_plugin_factory_catalog
from anyio.to_thread import run_sync
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute, APIRouter
from redis.asyncio import Redis
from sqlalchemy import text

from a13n_service.distribution import OSS, Distribution
from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.http import document_errors, install_error_envelope
from a13n_service.infra.ingress import BodyLimit
from a13n_service.infra.objects.interface import ObjectStore
from a13n_service.infra.objects.local import LocalObjects
from a13n_service.infra.objects.s3 import open_s3
from a13n_service.infra.sweeps import require_unique, run_sweeps
from a13n_service.infra.telemetry import open_instrumentation
from a13n_service.migrations.runner import heads, upgrade
from a13n_service.providers.environments import offered
from a13n_service.providers.registry import Registry
from a13n_service.resources.models.catalog import ModelsDevCatalog, catalog_channels
from a13n_service.runs.execute import execute
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.stream import ThreadHub
from a13n_service.runs.worker import Worker
from a13n_service.settings import Database, Objects, ProcessRole, Settings, load_settings
from a13n_service.tenancy.authenticate import COOKIE_NAME, CSRF_HEADER, SAFE_METHODS
from a13n_service.tenancy.requests import current_credential

EXEMPT_ROUTES = frozenset(
    {
        ("GET", "/healthz"),
        ("GET", "/readyz"),
        ("GET", "/api/v1/openapi.json"),
        ("GET", "/api/v1/docs"),
        ("GET", "/api/v1/docs/oauth2-redirect"),
    }
)


# How the local authenticator's credentials appear in the HTTP contract.
SECURITY_SCHEMES = {
    "apiKey": {"type": "http", "scheme": "bearer", "description": "A workspace API key (`a13n_...`)"},
    "loginSession": {"type": "apiKey", "in": "cookie", "name": COOKIE_NAME, "description": "A browser login session"},
    "csrf": {
        "type": "apiKey",
        "in": "header",
        "name": CSRF_HEADER,
        "description": "The login session's CSRF token, sent with it on every unsafe method",
    },
}


def _dependency_calls(dependant: Dependant) -> Iterator[Callable[..., Any] | None]:
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _dependency_calls(dependency)


def contract(app: FastAPI, *, locally_authenticated: Sequence[APIRouter]) -> dict[str, Any]:
    """The HTTP contract: FastAPI's document, with failures as the Service sends them, and how each authenticated
    operation of `locally_authenticated` (the routers the local authenticator protects) proves its caller."""
    if app.openapi_schema is not None:
        return app.openapi_schema
    document = FastAPI.openapi(app)  # FastAPI's own document, which it caches; completed in place
    document_errors(document)
    if locally_authenticated:
        document["components"]["securitySchemes"] = SECURITY_SCHEMES
    for router in locally_authenticated:
        for route in router.routes:
            if (
                not isinstance(route, APIRoute)
                or not route.include_in_schema
                or current_credential not in _dependency_calls(route.dependant)
            ):
                continue
            for method in route.methods or ():
                session = {"loginSession": []} if method in SAFE_METHODS else {"loginSession": [], "csrf": []}
                document["paths"][route.path_format][method.lower()]["security"] = [{"apiKey": []}, session]
    return document


async def check_schema(storage: Storage, expected: tuple[str, ...]) -> None:
    async with short_session(storage) as session:
        actual = tuple(
            (await session.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num"))).scalars()
        )
    if actual != tuple(sorted(expected)) or not expected:
        raise RuntimeError("Database schema is incompatible; run a13n-service migrate")


async def open_objects(stack: AsyncExitStack, config: Objects) -> ObjectStore:
    if config.backend == "local":
        return LocalObjects(config.root, max_bytes=config.max_bytes, timeout=config.timeout)
    assert config.bucket is not None
    return await stack.enter_async_context(
        open_s3(
            bucket=config.bucket,
            prefix=config.prefix,
            region=config.region,
            endpoint_url=config.endpoint_url,
            path_style=config.path_style,
            access_key_id=config.access_key_id.get_secret_value() if config.access_key_id else None,
            secret_access_key=config.secret_access_key.get_secret_value() if config.secret_access_key else None,
            max_bytes=config.max_bytes,
            timeout=config.timeout,
        )
    )


def open_storage(config: Database) -> Storage:
    """The process's database pool; the caller closes it."""
    return Storage(
        config.url.get_secret_value(),
        pool_size=config.pool_size,
        connect_timeout=config.connect_timeout,
        statement_timeout=config.statement_timeout,
    )


async def open_runtime(
    stack: AsyncExitStack, distribution: Distribution, config: Settings, *, executes: bool
) -> Runtime:
    storage = open_storage(config.database)
    stack.push_async_callback(storage.close)
    redis = Redis.from_url(
        config.redis.url.get_secret_value(),
        socket_timeout=config.redis.timeout,
        socket_connect_timeout=config.redis.timeout,
        decode_responses=True,
    )
    stack.push_async_callback(redis.aclose)
    telemetry = config.telemetry
    instrumentation = await stack.enter_async_context(
        open_instrumentation(
            telemetry.trace_config() if executes else None,
            metered=telemetry.metrics_port is not None,
            content=HarnessTraceContent(telemetry.trace_content),
        )
    )
    return Runtime(
        storage=storage,
        objects=await open_objects(stack, config.objects),
        redis=redis,
        keys=KeyRing(active_key_id=config.encryption.active_key_id, keys=config.encryption.keys),
        settings=config,
        registry=_registry(distribution, config),
        access=distribution.access(),
        plugins=build_harness_plugin_factory_catalog(plugin_keys=config.plugins.keys),
        admission=distribution.admission,
        instrumentation=instrumentation,
        # Backends hold no connection between queries, so the query side needs no lifecycle.
        traces=telemetry.trace_config(),
    )


def _registry(distribution: Distribution, config: Settings) -> Registry:
    environments = config.environments
    return Registry.of(
        offered(
            distribution.providers,
            allow_local=environments.allow_local,
            docker_host=environments.docker_host,
            docker_mount_roots=environments.docker_mount_roots,
        )
    )


def build_app(
    distribution: Distribution = OSS, *, role: ProcessRole = "all", settings: Settings | None = None
) -> FastAPI:
    config = settings or load_settings(extensions=distribution.settings)
    if role not in get_args(ProcessRole):
        raise ValueError(f"Unknown process role: {role}")
    # Invalid providers or roles fail here, before anything is served.
    _registry(distribution, config)
    distribution.access()
    expected = heads(distribution)
    serves_api, executes = role != "worker", role != "control"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if role != "worker" and config.database.auto_migrate:
            await run_sync(partial(upgrade, config.database, distribution))
        async with AsyncExitStack() as stack:
            runtime = await open_runtime(stack, distribution, config, executes=executes)
            app.state.runtime = runtime
            app.state.started = False
            background: list[asyncio.Task[None]] = []
            app.state.background = background
            try:
                async with asyncio.timeout(config.server.readiness_timeout):
                    await check_schema(runtime.storage, expected)
                if serves_api:
                    catalog = ModelsDevCatalog(
                        catalog_channels(runtime.registry.models.values()), runtime.endpoint_policy
                    )
                    app.state.model_catalog = catalog
                    background.append(asyncio.create_task(catalog.run(), name="model-catalog"))
                    app.state.thread_hub = ThreadHub(runtime)
                    background.append(asyncio.create_task(app.state.thread_hub.run(), name="thread-hub"))
                    sweeps = [factory(runtime) for factory in distribution.sweeps]
                    require_unique(sweeps)
                    background.append(asyncio.create_task(run_sweeps(sweeps), name="control-sweeps"))
                if executes:
                    background.append(asyncio.create_task(Worker(runtime, execute).run(), name="worker"))
                app.state.started = True
                yield
            finally:
                app.state.started = False
                for task in background:
                    task.cancel()
                async with asyncio.timeout(config.server.shutdown_timeout):
                    await asyncio.gather(*background, return_exceptions=True)

    app = FastAPI(
        title="a13n Service",
        version=version("a13n-service"),
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json" if serves_api else None,
        docs_url="/api/v1/docs" if serves_api else None,
        swagger_ui_oauth2_redirect_url="/api/v1/docs/oauth2-redirect" if serves_api else None,
        redoc_url=None,
    )
    app.add_middleware(BodyLimit, max_bytes=config.server.request_bytes, timeout=config.server.request_timeout)
    install_error_envelope(app)
    documented = distribution.routers if serves_api and distribution.authenticator is None else ()
    app.openapi = partial(contract, app, locally_authenticated=documented)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok", "role": role}

    @app.get("/readyz")
    async def ready() -> JSONResponse:
        """Ready while started and the database holds a usable schema. Redis only speeds work up, so losing it
        is reported as degraded rather than taking the replica out of service."""
        dependency = "runtime"
        try:
            if not getattr(app.state, "started", False) or any(task.done() for task in app.state.background):
                raise RuntimeError("not started")
            runtime: Runtime = app.state.runtime
            dependency = "database"
            async with asyncio.timeout(config.server.readiness_timeout):
                await check_schema(runtime.storage, expected)
        except Exception:
            return JSONResponse({"status": "unavailable", "dependency": dependency}, status_code=503)
        body: dict[str, object] = {"status": "ready", "role": role}
        try:
            async with asyncio.timeout(config.server.readiness_timeout):
                await runtime.redis.ping()
        except Exception:
            body["degraded"] = ["redis"]
        return JSONResponse(body)

    seen = set(EXEMPT_ROUTES)
    for router in distribution.routers:
        for route in router.routes:
            if not isinstance(route, APIRoute):
                raise ValueError("Distribution routers must declare direct HTTP API routes")
            for method in route.methods or ():
                if (method, route.path) in seen:
                    raise ValueError(f"Duplicate route: {method} {route.path}")
                seen.add((method, route.path))
        if serves_api:
            app.include_router(router)
    return app
