"""Organization App policy and HTTP admission before any App dependency executes."""

from collections.abc import Callable, Coroutine, Mapping
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.app_plugins import AppCatalog, AppPolicy
from a13n_service.infra.audit import record
from a13n_service.infra.db import lock, short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.http import IfMatch, require_match, tagged
from a13n_service.tenancy.access import OrganizationPath, administering, resolve_organization, workspace_scope
from a13n_service.tenancy.authorize import Principal, Scope, allowed_verbs, authorize
from a13n_service.tenancy.requests import Actor, CurrentRuntime, current_credential, current_workspace
from a13n_service.tenancy.tables import OrganizationRow


class FrontendView(BaseModel):
    bridge: int
    entry: str
    styles: list[str]


class AppView(BaseModel):
    app_id: str
    name: str
    distribution: str
    version: str
    enabled: bool
    factory_keys: list[str]
    frontend: FrontendView | None


class AppPlugins(BaseModel):
    # The policy's precondition is the ordinary organization ETag.
    id: str
    version: int
    permissions: list[str]
    items: list[AppView]


class AppUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def policy(settings: Mapping[str, JsonValue]) -> AppPolicy:
    return AppPolicy.model_validate(settings.get("app_plugins", {}))


def view(row: OrganizationRow, actor: Principal, catalog: AppCatalog) -> AppPlugins:
    selected = policy(row.settings)
    return AppPlugins(
        id=row.id,
        version=row.version,
        permissions=sorted(allowed_verbs(actor, Scope(row.id))),
        items=[
            AppView(
                app_id=app.app_id,
                name=app.name,
                distribution=app.distribution,
                version=app.version,
                enabled=app.app_id in selected.enabled,
                factory_keys=sorted(app.factory_keys),
                frontend=FrontendView(
                    bridge=app.frontend.bridge,
                    entry=app.asset_url(app.frontend.entry),
                    styles=[app.asset_url(path) for path in app.frontend.styles],
                )
                if app.app_id in selected.enabled and app.frontend is not None
                else None,
            )
            for app in catalog.apps
        ],
    )


async def readable(session: AsyncSession, actor: Principal, organization_id: str) -> OrganizationRow:
    row = await resolve_organization(session, actor, organization_id)
    authorize(actor, Scope(row.id), "read")
    return row


async def update(
    runtime: CurrentRuntime, actor: Principal, organization_id: str, app_id: str, enabled: bool, if_match: str | None
) -> AppPlugins:
    action = "app_plugin.update"
    async with administering(
        runtime.storage, runtime.access, actor, OrganizationPath(organization_id), action=action
    ) as (
        session,
        scope,
    ):
        row = await lock(session, OrganizationRow, scope.organization_id)
        assert row is not None
        require_match(if_match, row.id, row.version)
        current = policy(row.settings)
        selected = set(current.enabled)
        if enabled or app_id not in selected:
            runtime.apps.require(app_id)
        if (app_id in selected) != enabled:
            if enabled:
                selected.add(app_id)
            else:
                selected.discard(app_id)
            row.settings = {
                **row.settings,
                "app_plugins": AppPolicy(enabled=tuple(sorted(selected))).model_dump(mode="json"),
            }
            await session.flush()
            record(
                session,
                scope,
                actor_id=actor.id,
                action=action,
                target_kind="app_plugin",
                target_id=app_id,
                details={"enabled": enabled},
            )
        return view(row, actor, runtime.apps)


router = APIRouter(prefix="/api/v1/organizations", tags=["app-plugins"])


@router.get("/{organization_id}/app-plugins", response_model=AppPlugins)
async def list_app_plugins(
    organization_id: str, response: Response, actor: Actor, runtime: CurrentRuntime
) -> AppPlugins:
    """Organization administration is intentionally outside the MCP management surface."""
    async with short_session(runtime.storage) as session:
        result = view(await readable(session, actor, organization_id), actor, runtime.apps)
    return tagged(response, result)


@router.put("/{organization_id}/app-plugins/{app_id}", response_model=AppPlugins)
async def update_app_plugin(
    organization_id: str,
    app_id: str,
    body: AppUpdate,
    response: Response,
    actor: Actor,
    runtime: CurrentRuntime,
    if_match: IfMatch = None,
) -> AppPlugins:
    return tagged(response, await update(runtime, actor, organization_id, app_id, body.enabled, if_match))


class AppRoute(APIRoute):
    """Owner-closed route classes survive FastAPI include/compilation. The gate wraps dependency resolution."""

    app_plugin_id: str

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        app_id = self.app_plugin_id

        async def guarded(request: Request) -> Response:
            runtime = request.app.state.runtime
            runtime.apps.require(app_id)
            credential = await current_credential(request, runtime)
            workspace_id = await current_workspace(credential.principal, request.headers.get("X-Workspace-ID"))
            async with short_session(runtime.storage) as session:
                scope = await workspace_scope(session, credential.principal, workspace_id, "read")
                organization = await session.get_one(OrganizationRow, scope.organization_id)
                enabled = app_id in policy(organization.settings).enabled
            if not enabled:
                raise ServiceError("disabled", "App plugin is disabled for this organization", {"app_id": app_id})
            return await handler(request)

        return guarded


def app_router(app_id: str) -> APIRouter:
    """Declare business endpoints below a literal App namespace; the installed catalog verifies ownership."""

    class OwnedRoute(AppRoute):
        app_plugin_id = app_id

    return APIRouter(
        prefix=f"/api/v1/apps/{app_id}",
        route_class=OwnedRoute,
        tags=[f"app:{app_id}"],
        dependencies=[Depends(current_credential)],
    )
