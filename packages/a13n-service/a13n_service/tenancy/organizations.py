"""Organizations: the caller's organizations, their names and icons."""

from collections.abc import Mapping

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors, images
from a13n_service.infra.audit import record
from a13n_service.infra.db import Storage, assign, lock, short_session
from a13n_service.infra.http import require_match
from a13n_service.infra.objects.interface import ObjectStore
from a13n_service.tenancy.access import Access, AdminPath, OrganizationPath, administering, resolve_organization
from a13n_service.tenancy.authorize import Principal, Scope, allowed_verbs, authorize
from a13n_service.tenancy.schemas import Organization, OrganizationPage, OrganizationUpdate
from a13n_service.tenancy.tables import OrganizationRow, WorkspaceRow

_UPDATE = "organization.update"


def organization_view(row: OrganizationRow, actor: Principal) -> Organization:
    return Organization(
        id=row.id,
        name=row.name,
        image_url=images.url(f"/api/v1/organizations/{row.id}/icon", row.image),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        permissions=sorted(allowed_verbs(actor, Scope(row.id))),
    )


async def list_organizations(storage: Storage, actor: Principal, *, limit: int, cursor: str | None) -> OrganizationPage:
    """Organizations where the caller holds any grant; an API key sees only its own organization."""
    member_of = {grant.organization_id for grant in actor.grants}
    if actor.confinement is not None:
        member_of &= {actor.confinement.organization_id}
    async with short_session(storage) as session:
        rows, next_cursor = await cursors.id_page(
            session,
            select(OrganizationRow).where(OrganizationRow.id.in_(member_of)),
            OrganizationRow.id,
            kind="organizations",
            owner=actor.id,
            cursor=cursor,
            limit=limit,
        )
    return OrganizationPage(items=[organization_view(row, actor) for row in rows], next_cursor=next_cursor)


async def get_organization(storage: Storage, actor: Principal, organization_id: str) -> Organization:
    async with short_session(storage) as session:
        return organization_view(await _readable(session, actor, organization_id), actor)


async def update_organization(
    storage: Storage,
    access: Access,
    actor: Principal,
    organization_id: str,
    body: OrganizationUpdate,
    *,
    if_match: str | None,
) -> Organization:
    changes = body.model_dump(exclude_none=True)
    return await _change(storage, access, actor, organization_id, changes, if_match=if_match)


async def change_icon(
    storage: Storage,
    access: Access,
    objects: ObjectStore,
    actor: Principal,
    organization_id: str,
    data: bytes | None,
    *,
    if_match: str | None,
) -> Organization:
    """Replace the icon with `data`, or remove it with None."""
    path = OrganizationPath(organization_id)
    image = await store_icon(storage, access, objects, actor, path, data, if_match=if_match, action=_UPDATE)
    return await _change(storage, access, actor, organization_id, {"image": image}, if_match=if_match)


async def store_icon(
    storage: Storage,
    access: Access,
    objects: ObjectStore,
    actor: Principal,
    path: AdminPath,
    data: bytes | None,
    *,
    if_match: str | None,
    action: str,
) -> dict[str, JsonValue] | None:
    """The stored image of an organization's or workspace's new icon, None for no icon. The change's own checks
    run first, so bytes are stored only while the actor may make it (an archived workspace refuses) and the
    caller's ETag is current."""
    if data is None:
        return None
    async with administering(storage, access, actor, path, action=action) as (session, scope):
        owner = WorkspaceRow if scope.workspace_id is not None else OrganizationRow
        row = await session.get_one(owner, scope.workspace_id or scope.organization_id)
        require_match(if_match, row.id, row.version)
        prefix = images.prefix_for(row.id, organization_id=scope.organization_id)
    return await images.store(objects, prefix, data)


async def _change(
    storage: Storage,
    access: Access,
    actor: Principal,
    organization_id: str,
    changes: Mapping[str, object],
    *,
    if_match: str | None,
) -> Organization:
    async with administering(storage, access, actor, OrganizationPath(organization_id), action=_UPDATE) as (
        session,
        scope,
    ):
        row = await _lock(session, scope.organization_id, if_match)
        await _save(session, row, assign(row, changes), actor_id=actor.id)
        return organization_view(row, actor)


async def get_icon(storage: Storage, actor: Principal, organization_id: str) -> dict[str, JsonValue] | None:
    async with short_session(storage) as session:
        return (await _readable(session, actor, organization_id)).image


async def _readable(session: AsyncSession, actor: Principal, organization_id: str) -> OrganizationRow:
    row = await resolve_organization(session, actor, organization_id)
    authorize(actor, Scope(row.id), "read")
    return row


async def _lock(session: AsyncSession, organization_id: str, if_match: str | None) -> OrganizationRow:
    row = await lock(session, OrganizationRow, organization_id)
    assert row is not None
    require_match(if_match, row.id, row.version)
    return row


async def _save(session: AsyncSession, row: OrganizationRow, changed: list[str], *, actor_id: str) -> None:
    """Write and audit the changed fields."""
    if not changed:
        return
    await session.flush()
    record(
        session,
        Scope(row.id),
        actor_id=actor_id,
        action=_UPDATE,
        target_kind="organization",
        target_id=row.id,
        details={"fields": [*changed]},
    )
