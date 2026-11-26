"""App assembly adapters over the existing runtime, exit stack, HTTP router and sweep scheduler."""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any
from uuid import uuid4

from anyio.to_thread import run_sync
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from a13n_service.app_definitions import InstalledApp, OrganizationSweep
from a13n_service.app_plugins import AppCatalog, AppManifest
from a13n_service.app_sdk.hooks import HookScope
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import not_found
from a13n_service.infra.sweeps import Sweep
from a13n_service.infra.telemetry import meter
from a13n_service.runs import seal_events
from a13n_service.runs.environments import events as environment_events
from a13n_service.runs.environments.app_hooks import invoke
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.app_plugins import AppRoute, AppWebhookRoute, policy
from a13n_service.tenancy.tables import OrganizationRow

SEAL_DROPS = meter.create_counter("a13n.app.run_seal.dropped", unit="{hint}")
ENVIRONMENT_DROPS = meter.create_counter("a13n.app.environment_event.dropped", unit="{hint}")
STALE_CONSUMER_IDLE_MS = 300_000
_MAX_PRUNE_PER_PASS = 100


def app_routers(apps: tuple[InstalledApp, ...]) -> tuple[APIRouter, ...]:
    routers = []
    for app in apps:
        prefix = f"/api/v1/apps/{app.app_id}/"
        for router in app.definition.routers:
            for route in router.routes:
                if (
                    not isinstance(route, AppRoute)
                    or route.app_plugin_id != app.app_id
                    or not route.path.startswith(prefix)
                ):
                    raise ValueError(f"App {app.app_id} must use its own app_router for direct HTTP routes")
            routers.append(router)
    return tuple(routers)


def app_webhook_routers(apps: tuple[InstalledApp, ...]) -> tuple[APIRouter, ...]:
    routers = []
    for app in apps:
        prefix = f"/api/v1/app-webhooks/{app.app_id}/"
        for router in app.definition.webhook_routers:
            for route in router.routes:
                if (
                    not isinstance(route, AppWebhookRoute)
                    or route.app_plugin_id != app.app_id
                    or not route.path.startswith(prefix)
                ):
                    raise ValueError(f"App {app.app_id} must use its own app_webhook_router for webhook routes")
                if (route.openapi_extra or {}).get("x-a13n-mcp"):
                    raise ValueError(f"App {app.app_id} webhook routes cannot be admitted to management MCP")
            routers.append(router)
    return tuple(routers)


def open_assets(
    stack: AsyncExitStack, catalog: AppCatalog, dev_app: str | None = None, dev_assets: Path | None = None
) -> dict[str, Path]:
    roots = {}
    for app in catalog.apps:
        frontend = app.frontend
        if frontend is None:
            continue
        directory = (
            dev_assets
            if app.app_id == dev_app and dev_assets is not None
            else stack.enter_context(as_file(files(frontend.package).joinpath(frontend.directory)))
        )
        root = directory.resolve()
        for path in (frontend.entry, *frontend.styles):
            asset = (root / path).resolve()
            if not asset.is_relative_to(root) or not asset.is_file():
                raise ValueError(f"App {app.app_id} asset is missing: {path}")
        roots[app.app_id] = root
    return roots


def _asset(roots: Mapping[str, Path], app: AppManifest, version: str, path: str) -> Path:
    root = roots.get(app.app_id)
    if root is not None and version == app.version:
        try:
            asset = (root / path).resolve()
            if asset.is_relative_to(root) and asset.is_file():
                return asset
        except (OSError, ValueError):
            pass
    raise not_found("app_asset", path)


router = APIRouter()


@router.get("/api/v1/app-plugins/{app_id}/assets/{version}/{path:path}", include_in_schema=False)
async def app_asset(request: Request, app_id: str, version: str, path: str) -> FileResponse:
    """Public packaged code follows Console asset access policy; tenant business endpoints remain guarded."""
    app = request.app.state.runtime.apps.require(app_id)
    asset = await run_sync(lambda: _asset(request.app.state.app_assets, app, version, path))
    return FileResponse(
        asset,
        headers={
            "Cache-Control": (
                "no-store" if app_id == request.app.state.dev_app else "public, max-age=31536000, immutable"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@dataclass
class _Organizations:
    runtime: Runtime
    app_id: str
    sweep: OrganizationSweep
    cursor: str = ""

    async def __call__(self) -> None:
        # A per-sweep keyset cursor bounds work and prevents large organizations lists starving later IDs.
        async with short_session(self.runtime.storage) as session:
            ids = list(
                await session.scalars(
                    select(OrganizationRow.id)
                    .where(
                        OrganizationRow.id > self.cursor,
                        OrganizationRow.settings["app_plugins"]["enabled"].contains([self.app_id]),
                    )
                    .order_by(OrganizationRow.id)
                    .limit(self.runtime.settings.control.sweep_batch)
                )
            )
        if not ids:
            self.cursor = ""
        failures: list[Exception] = []
        for organization_id in ids:
            self.cursor = organization_id
            try:
                # Each organization is a new admission, including after a previous callback awaited I/O.
                async with short_session(self.runtime.storage) as session:
                    organization = await session.get_one(OrganizationRow, organization_id)
                    enabled = self.app_id in policy(organization.settings).enabled
                if enabled:
                    async with asyncio.timeout(self.sweep.timeout):
                        await self.sweep.run(self.runtime, organization_id)
            except Exception as error:
                error.add_note(f"App {self.app_id} sweep {self.sweep.name}, organization {organization_id}")
                failures.append(error)
        if failures:
            raise ExceptionGroup("App organization sweep failed", failures)


def app_sweeps(runtime: Runtime, apps: tuple[InstalledApp, ...]) -> list[Sweep]:
    admission_timeout = runtime.settings.database.connect_timeout + runtime.settings.database.statement_timeout
    # One scan plus one policy read and bounded callback per selected organization.
    return [
        Sweep(
            name=f"app:{app.app_id}:{sweep.name}",
            every=sweep.every,
            timeout=admission_timeout + runtime.settings.control.sweep_batch * (admission_timeout + sweep.timeout),
            run=_Organizations(runtime, app.app_id, sweep),
        )
        for app in apps
        for sweep in app.definition.sweeps
    ]


async def _prune_stale_consumers(runtime: Runtime, current: str, stream_key: str, group: str) -> None:
    """Drop dead control consumer metadata left behind after ungraceful exits."""
    threshold = STALE_CONSUMER_IDLE_MS
    consumers: Any = await runtime.redis.xinfo_consumers(stream_key, group)
    deleted = 0
    for info in consumers:
        if deleted >= _MAX_PRUNE_PER_PASS:
            break
        name = info.get("name")
        idle = info.get("idle")
        pending = info.get("pending", 0)
        if isinstance(name, str) and name != current and isinstance(idle, int) and idle >= threshold and pending == 0:
            await runtime.redis.xgroup_delconsumer(stream_key, group, name)
            deleted += 1


@dataclass(frozen=True)
class _Subscription[E: (seal_events.RunSealEvent, environment_events.EnvironmentEvent)]:
    app_id: str
    scope: HookScope
    timeout: float
    matches: Callable[[E], bool]
    run: Callable[[Runtime, E], Awaitable[object]]


async def app_run_seals(runtime: Runtime, apps: tuple[InstalledApp, ...]) -> None:
    subscriptions = [
        _Subscription(app.app_id, sub.scope, sub.timeout, lambda event, sub=sub: event.status in sub.statuses, sub.run)
        for app in apps
        for sub in app.definition.run_seals
    ]
    await _receive(runtime, seal_events.STREAM_KEY, seal_events.GROUP, seal_events.RunSealEvent, subscriptions)


async def app_environment_events(runtime: Runtime, apps: tuple[InstalledApp, ...]) -> None:
    subscriptions = [
        _Subscription(app.app_id, sub.scope, sub.timeout, lambda event, sub=sub: event.event in sub.events, sub.run)
        for app in apps
        for sub in app.definition.environment_events
    ]
    await _receive(
        runtime,
        environment_events.STREAM_KEY,
        environment_events.GROUP,
        environment_events.EnvironmentEvent,
        subscriptions,
    )


async def _receive[E: (seal_events.RunSealEvent, environment_events.EnvironmentEvent)](
    runtime: Runtime,
    stream_key: str,
    group: str,
    event_type: type[E],
    subscriptions: list[_Subscription[E]],
) -> None:
    """Receive lossy cross-role hints; drop excess work and rely on App reconciliation."""
    from a13n_logging import exception_details, get_logger
    from pydantic import ValidationError
    from redis.exceptions import RedisError, ResponseError

    logger = get_logger(__name__)
    if not subscriptions:
        return
    consumer = f"control-{uuid4().hex[:12]}"
    block_ms = min(1000, max(1, int(runtime.settings.redis.timeout * 500)))
    running: set[asyncio.Task[None]] = set()
    app_running: dict[str, set[asyncio.Task[None]]] = {sub.app_id: set() for sub in subscriptions}

    async def deliver(subscription: _Subscription[E], event: E) -> None:
        try:
            admission = runtime.settings.database.connect_timeout + runtime.settings.database.statement_timeout
            async with asyncio.timeout(admission):
                async with short_session(runtime.storage) as session:
                    organization = await session.get(OrganizationRow, event.organization_id)
                    enabled = organization is not None and subscription.app_id in policy(organization.settings).enabled
            if not enabled:
                return
            if isinstance(event, environment_events.EnvironmentEvent):
                await invoke(
                    lambda: subscription.run(runtime, event),
                    app_id=subscription.app_id,
                    phase="event",
                    timeout=subscription.timeout,
                    target_id=event.environment_id,
                    abortable=False,
                )
            else:
                async with asyncio.timeout(subscription.timeout):
                    await subscription.run(runtime, event)
        except Exception as error:
            logger.warning(
                "App event delivery failed",
                extra={"app_id": subscription.app_id, "stream": stream_key, "error_type": type(error).__name__},
            )

    try:
        while True:
            try:
                try:
                    await runtime.redis.xgroup_create(stream_key, group, id="$", mkstream=True)
                except ResponseError as error:
                    if not str(error).startswith("BUSYGROUP"):
                        raise
                await _prune_stale_consumers(runtime, consumer, stream_key, group)
                next_prune = time.monotonic() + STALE_CONSUMER_IDLE_MS / 1000
                while True:
                    batches: Any = await runtime.redis.xreadgroup(
                        group, consumer, {stream_key: ">"}, count=32, block=block_ms, noack=True
                    )
                    if time.monotonic() >= next_prune:
                        await _prune_stale_consumers(runtime, consumer, stream_key, group)
                        next_prune = time.monotonic() + STALE_CONSUMER_IDLE_MS / 1000
                    for _, entries in batches:
                        for _, fields in entries:
                            try:
                                event = event_type.model_validate_json(fields["data"])
                            except (KeyError, ValidationError, ValueError, TypeError):
                                continue
                            for subscription in subscriptions:
                                app_id = subscription.app_id
                                if not subscription.matches(event) or (
                                    subscription.scope == "app" and event.app_id != app_id
                                ):
                                    continue
                                active = app_running[app_id]
                                if len(active) >= 32:
                                    drops = SEAL_DROPS if stream_key == seal_events.STREAM_KEY else ENVIRONMENT_DROPS
                                    drops.add(1, {"stage": "delivery", "app_id": app_id})
                                    continue
                                task = asyncio.create_task(deliver(subscription, event))
                                running.add(task)
                                active.add(task)
                                task.add_done_callback(running.discard)
                                task.add_done_callback(active.discard)
            except RedisError as error:
                logger.warning(
                    "App event stream disconnected",
                    extra={"stream": stream_key, "exception_details": exception_details(error)},
                )
                await asyncio.sleep(1)
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
        try:
            async with asyncio.timeout(min(runtime.settings.redis.timeout, 0.5)):
                await runtime.redis.xgroup_delconsumer(stream_key, group, consumer)
        except (RedisError, TimeoutError):
            pass
