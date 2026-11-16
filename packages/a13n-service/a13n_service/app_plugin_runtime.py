"""App assembly adapters over the existing runtime, exit stack, HTTP router and sweep scheduler."""

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


def open_assets(stack: AsyncExitStack, catalog: AppCatalog) -> dict[str, Path]:
    roots = {}
    for app in catalog.apps:
        frontend = app.frontend
        if frontend is None:
            continue
        root = stack.enter_context(as_file(files(frontend.package).joinpath(frontend.directory))).resolve()
        for path in (frontend.entry, *frontend.styles):
            asset = (root / path).resolve()
            if not asset.is_relative_to(root) or not asset.is_file():
                raise ValueError(f"App {app.app_id} asset is missing: {path}")
        roots[app.app_id] = root
    return roots


def _asset(roots: Mapping[str, Path], app: AppManifest, version: str, path: str) -> Path:
    root = roots.get(app.app_id)
    if root is not None and version == app.version:
        asset = (root / path).resolve()
        if asset.is_relative_to(root) and asset.is_file():
            return asset
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
            "Cache-Control": "public, max-age=31536000, immutable",
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
        for organization_id in ids:
            self.cursor = organization_id
            # Each organization is a new admission, including after a previous callback awaited I/O.
            async with short_session(self.runtime.storage) as session:
                organization = await session.get_one(OrganizationRow, organization_id)
                enabled = self.app_id in policy(organization.settings).enabled
            if enabled:
                await self.sweep.run(self.runtime, organization_id)


def app_sweeps(runtime: Runtime, apps: tuple[InstalledApp, ...]) -> list[Sweep]:
    return [
        Sweep(
            name=f"app:{app.app_id}:{sweep.name}",
            every=sweep.every,
            timeout=sweep.timeout,
            run=_Organizations(runtime, app.app_id, sweep),
        )
        for app in apps
        for sweep in app.definition.sweeps
    ]
