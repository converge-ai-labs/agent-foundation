"""Provision and check native workspace resources for an App project."""

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.app_provenance import AppProvenance
from a13n_service.resources.memories.schemas import MemorySpaceCreate
from a13n_service.resources.memories.spaces import insert_space
from a13n_service.runs.browsers.lifecycle import authorize_use, find, usable
from a13n_service.runs.browsers.schemas import BrowserCreate
from a13n_service.runs.browsers.service import insert_managed
from a13n_service.runs.environments.access import audit
from a13n_service.runs.environments.access import find as find_environment
from a13n_service.runs.environments.lifecycle import begin, reserve
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.authorize import authorize

from ._access import app_actor, app_workspace, run_actor


async def reserve_environment(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    app_context: dict[str, str] | None = None,
    principal_id: str,
    template_id: str,
    name: str | None = None,
) -> str:
    """Reserve a managed Environment and queue its creation in the caller's transaction."""
    scope = await app_workspace(session, runtime, workspace_id, app_id)
    actor = await run_actor(session, runtime, scope, principal_id)
    row = await reserve(
        session,
        actor,
        scope,
        template_id,
        limit=runtime.settings.environments.managed_count,
        name=name,
        provenance=AppProvenance(app_id, app_context),
    )
    await begin(session, row, "creating")
    await session.flush()
    audit(session, actor, row, "create")
    return row.id


async def reserve_browser(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    app_context: dict[str, str] | None = None,
    principal_id: str,
    provider_id: str,
    name: str,
    recipe: dict[str, JsonValue] | None = None,
) -> str:
    """Reserve a managed Browser for preparation on first use, in the caller's transaction."""
    scope = await app_workspace(session, runtime, workspace_id, app_id)
    actor = await run_actor(session, runtime, scope, principal_id)
    row = await insert_managed(
        session,
        runtime,
        actor,
        scope,
        BrowserCreate(provider_id=provider_id, name=name, recipe=recipe or {}),
        provenance=AppProvenance(app_id, app_context),
    )
    return row.id


async def create_memory_space(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    *,
    app_id: str,
    app_context: dict[str, str] | None = None,
    actor_id: str,
    name: str,
    description: str | None = None,
) -> str:
    """Create a Memory Space and its file Memory under native workspace write authority."""
    scope = await app_workspace(session, runtime, workspace_id, app_id)
    actor = await app_actor(session, runtime, scope, actor_id)
    authorize(actor, scope, "write")
    return (
        await insert_space(
            session,
            actor,
            scope,
            MemorySpaceCreate(name=name, description=description),
            settings=runtime.settings.memory,
            provenance=AppProvenance(app_id, app_context),
        )
    ).id


async def check_resources(
    session: AsyncSession,
    runtime: Runtime,
    workspace_id: str,
    *,
    principal_id: str,
    environment_id: str | None = None,
    browser_id: str | None = None,
) -> None:
    """Check current usability as the workspace execution account, without creating a binding."""
    from a13n_service.infra.errors import not_found
    from a13n_service.resources.environment_instances.access import require_usable
    from a13n_service.tenancy.authorize import WorkspaceScope
    from a13n_service.tenancy.tables import WorkspaceRow

    workspace = await session.get(WorkspaceRow, workspace_id)
    if workspace is None:
        raise not_found("workspace", workspace_id)
    scope = WorkspaceScope(workspace.organization_id, workspace_id)
    actor = await run_actor(session, runtime, scope, principal_id, share=False)
    if browser_id is not None:
        browser = await find(session, workspace_id, browser_id)
        usable(browser)
        await authorize_use(session, actor, browser, scope)
    if environment_id is not None:
        require_usable(await find_environment(session, scope, environment_id), actor.id)
