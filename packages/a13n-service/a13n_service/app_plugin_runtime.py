"""App assembly adapters over the existing runtime, exit stack, HTTP router and sweep scheduler."""

import asyncio
from collections.abc import Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path

from anyio.to_thread import run_sync
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from a13n_service.app_definitions import InstalledApp, OrganizationSweep
from a13n_service.app_plugins import AppCatalog, AppManifest
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import not_found
from a13n_service.infra.sweeps import Sweep
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.app_plugins import AppRoute, policy
from a13n_service.tenancy.tables import OrganizationRow


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


async def app_run_seals(runtime: Runtime, apps: tuple[InstalledApp, ...]) -> None:
    """Receive lossy cross-role hints; drop excess work and rely on App reconciliation."""
    from a13n_logging import exception_details, get_logger
    from pydantic import ValidationError
    from redis.exceptions import RedisError

    from a13n_service.runs.seal_events import RunSealEvent, channel

    logger = get_logger(__name__)
    subscriptions = [(app.app_id, sub) for app in apps for sub in app.definition.run_seals]
    if not subscriptions:
        return
    running: set[asyncio.Task[None]] = set()

    async def deliver(app_id: str, subscription: object, event: RunSealEvent) -> None:
        from a13n_service.app_definitions import RunSealSubscription

        assert isinstance(subscription, RunSealSubscription)
        try:
            admission = runtime.settings.database.connect_timeout + runtime.settings.database.statement_timeout
            async with asyncio.timeout(admission + subscription.timeout):
                async with short_session(runtime.storage) as session:
                    organization = await session.get(OrganizationRow, event.organization_id)
                    enabled = organization is not None and app_id in policy(organization.settings).enabled
                if enabled:
                    async with asyncio.timeout(subscription.timeout):
                        await subscription.run(runtime, event)
        except Exception as error:
            logger.warning(
                "App Run seal callback failed", extra={"app_id": app_id, "exception_details": exception_details(error)}
            )

    try:
        while True:
            try:
                async with runtime.redis.pubsub() as listener:
                    await listener.subscribe(channel(runtime))
                    while True:
                        message = await listener.get_message(ignore_subscribe_messages=True, timeout=1)
                        if not message:
                            continue
                        try:
                            event = RunSealEvent.model_validate_json(message["data"])
                        except (ValidationError, ValueError, TypeError):
                            continue
                        for app_id, subscription in subscriptions:
                            if event.status in subscription.statuses and len(running) < 32:
                                task = asyncio.create_task(deliver(app_id, subscription, event))
                                running.add(task)
                                task.add_done_callback(running.discard)
            except RedisError as error:
                logger.warning(
                    "App Run seal subscription disconnected", extra={"exception_details": exception_details(error)}
                )
                await asyncio.sleep(1)
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)
