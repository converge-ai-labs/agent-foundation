"""Workspaces: creation, reads with the caller's verbs, renaming and icons, and archiving (read-only from then
on)."""

from collections.abc import Awaitable, Callable, Mapping
from functools import partial

from pydantic import JsonValue
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors, images
from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, after_commit, assign, lock, now, short_session
from a13n_service.infra.errors import conflict, not_found
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.objects.interface import ObjectStore
from a13n_service.tenancy.access import (
    Access,
    OrganizationPath,
    WorkspacePath,
    administering,
    administering_workspace,
    readable_workspaces,
    resolve_organization,
    resolve_workspace,
    workspace_scope,
)
from a13n_service.tenancy.authorize import Principal, Scope, Verb, allowed_verbs, authorize
from a13n_service.tenancy.organizations import store_icon
from a13n_service.tenancy.schemas import Workspace, WorkspaceCreate, WorkspacePage, WorkspaceUpdate
from a13n_service.tenancy.tables import InvitationRow, WorkspaceRow

type WorkspaceCreated = Callable[[str], Awaitable[None]]

_UPDATE = "workspace.update"


async def insert_workspace(
    session: AsyncSession,
    *,
    workspace_id: str,
    organization_id: str,
    name: str,
    on_created: WorkspaceCreated | None = None,
) -> WorkspaceRow:
    """The shared insertion point; initialization runs only after the committing session closes."""
    row = WorkspaceRow(id=workspace_id, organization_id=organization_id, name=name)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    if on_created is not None:
        after_commit(session, partial(on_created, row.id))
    return row


def workspace_view(row: WorkspaceRow, actor: Principal) -> Workspace:
    permissions: frozenset[Verb] = allowed_verbs(actor, Scope(row.organization_id, row.id))
    if row.archived_at is not None:
        permissions &= {"read"}
    return Workspace(
        id=row.id,
        organization_id=row.organization_id,
        name=row.name,
        settings=row.settings,
        image_url=images.url(f"/api/v1/workspaces/{row.id}/icon", row.image),
        archived_at=row.archived_at,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        permissions=sorted(permissions),
    )


async def create_workspace(
    storage: Storage,
    access: Access,
    actor: Principal,
    organization_id: str,
    body: WorkspaceCreate,
    *,
    on_created: WorkspaceCreated | None = None,
) -> Workspace:
    path = OrganizationPath(organization_id)
    async with administering(storage, access, actor, path, action="workspace.create") as (session, scope):
        row = await insert_workspace(
            session,
            workspace_id=new_object_id("ws"),
            organization_id=scope.organization_id,
            name=body.name,
            on_created=on_created,
        )
        record(
            session,
            Scope(row.organization_id, row.id),
            actor_id=actor.id,
            action="workspace.create",
            target_kind="workspace",
            target_id=row.id,
        )
        return workspace_view(row, actor)


async def list_workspaces(
    storage: Storage, actor: Principal, *, organization_id: str | None = None, limit: int, cursor: str | None
) -> WorkspacePage:
    """Workspaces the caller can read, archived ones included; across organizations unless one is given."""
    async with short_session(storage) as session:
        query = select(WorkspaceRow).where(WorkspaceRow.id.in_(readable_workspaces(actor)))
        owner = actor.id
        if organization_id is not None:
            organization = await resolve_organization(session, actor, organization_id)
            authorize(actor, Scope(organization.id), "read")
            query, owner = query.where(WorkspaceRow.organization_id == organization.id), organization.id
        rows, next_cursor = await cursors.id_page(
            session, query, WorkspaceRow.id, kind="workspaces", owner=owner, cursor=cursor, limit=limit
        )
    return WorkspacePage(items=[workspace_view(row, actor) for row in rows], next_cursor=next_cursor)


async def get_workspace(storage: Storage, actor: Principal, workspace_id: str) -> Workspace:
    async with short_session(storage) as session:
        return workspace_view(await _readable(session, actor, workspace_id), actor)


async def update_workspace(
    storage: Storage,
    access: Access,
    actor: Principal,
    workspace_id: str,
    body: WorkspaceUpdate,
    *,
    if_match: str | None,
) -> Workspace:
    return await _change(storage, access, actor, workspace_id, body.model_dump(exclude_none=True), if_match=if_match)


async def change_icon(
    storage: Storage,
    access: Access,
    objects: ObjectStore,
    actor: Principal,
    workspace_id: str,
    data: bytes | None,
    *,
    if_match: str | None,
) -> Workspace:
    """Replace the icon with `data`, or remove it with None."""
    path = WorkspacePath(workspace_id)
    image = await store_icon(storage, access, objects, actor, path, data, if_match=if_match, action=_UPDATE)
    return await _change(storage, access, actor, workspace_id, {"image": image}, if_match=if_match)


async def get_icon(storage: Storage, actor: Principal, workspace_id: str) -> dict[str, JsonValue] | None:
    async with short_session(storage) as session:
        return (await _readable(session, actor, workspace_id)).image


async def archive_workspace(
    storage: Storage, access: Access, actor: Principal, workspace_id: str, *, if_match: str | None
) -> Workspace:
    """Archiving is organization administration; the workspace refuses every verb but read afterwards, and its
    pending invitations are revoked."""
    async with short_session(storage) as session:
        target = await resolve_workspace(session, actor, workspace_id)
    path = OrganizationPath(target.organization_id)
    async with administering(storage, access, actor, path, action="workspace.archive") as (session, _):
        row = await _lock_workspace(session, target.id, if_match)
        if row.archived_at is not None:
            raise conflict("workspace", row.id, "archived")
        row.archived_at = await now(session)
        revoked = await session.scalars(
            update(InvitationRow)
            .where(
                InvitationRow.workspace_id == row.id,
                InvitationRow.accepted_at.is_(None),
                InvitationRow.revoked_at.is_(None),
            )
            .values(revoked_at=row.archived_at)
            .returning(InvitationRow.id)
        )
        await session.flush()
        record(
            session,
            Scope(row.organization_id, row.id),
            actor_id=actor.id,
            action="workspace.archive",
            target_kind="workspace",
            target_id=row.id,
            details={"revoked_invitations": len(revoked.all())},
        )
        return workspace_view(row, actor)


async def _change(
    storage: Storage,
    access: Access,
    actor: Principal,
    workspace_id: str,
    changes: Mapping[str, object],
    *,
    if_match: str | None,
) -> Workspace:
    async with administering_workspace(storage, access, actor, workspace_id, action=_UPDATE) as (session, scope):
        row = await _lock_workspace(session, scope.workspace_id, if_match)
        changed = assign(row, changes)
        if changed:
            await session.flush()
            record(
                session,
                scope,
                actor_id=actor.id,
                action=_UPDATE,
                target_kind="workspace",
                target_id=row.id,
                details={"fields": [*changed]},
            )
        return workspace_view(row, actor)


async def _readable(session: AsyncSession, actor: Principal, workspace_id: str) -> WorkspaceRow:
    scope = await workspace_scope(session, actor, workspace_id, "read")
    return await session.get_one(WorkspaceRow, scope.workspace_id)


async def _lock_workspace(session: AsyncSession, workspace_id: str, if_match: str | None) -> WorkspaceRow:
    row = await lock(session, WorkspaceRow, workspace_id)
    if row is None:
        raise not_found("workspace", workspace_id)
    require_match(if_match, row.id, row.version)
    return row
