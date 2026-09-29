"""Assets are created from staged uploads; retirement blocks new use and keeps content readable."""

from collections.abc import Mapping

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, now, short_session, transaction, violated_constraint
from a13n_service.infra.errors import ServiceError, conflict
from a13n_service.infra.http import require_match
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.objects.interface import ObjectRef, ObjectStore, read
from a13n_service.resources.assets.schemas import Asset, AssetCreate, AssetPage
from a13n_service.resources.assets.tables import AssetRow
from a13n_service.resources.rows import audit_row, find_row
from a13n_service.resources.uploads import service as uploads
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


def _replay(row: AssetRow, body: AssetCreate) -> Asset:
    if row.name != body.name:
        raise conflict(row.KIND, row.id, "upload_in_use")
    return Asset.model_validate(row)


async def create_asset(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    body: AssetCreate,
    *,
    source: dict[str, JsonValue] | None = None,
) -> tuple[Asset, bool]:
    """Create from a staged upload; the same upload and name read back the existing asset (False).

    `source` records the run, attempt and tool call that produced the content, when a run did.
    """
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        # An upload row exists only once its bytes are stored; reads verify them against its digest.
        upload = await uploads.find(session, scope.workspace_id, body.upload_id)
    reference = uploads.object_key(scope.organization_id, upload.id)
    existing = select(AssetRow).where(AssetRow.workspace_id == scope.workspace_id, AssetRow.content_ref == reference)
    try:
        async with transaction(storage) as session:
            await workspace_scope(session, actor, scope.workspace_id, "write")
            if (row := await session.scalar(existing)) is not None:
                return _replay(row, body), False
            row = AssetRow(
                id=new_object_id("ast"),
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
                name=body.name,
                content_type=upload.content_type,
                size=upload.size,
                digest=upload.digest,
                content_ref=reference,
                source=source,
                created_by_id=actor.id,
            )
            session.add(row)
            audit_row(session, actor, row, "create")
            await session.flush()
            return Asset.model_validate(row), True
    except IntegrityError as error:
        if violated_constraint(error) != "uq_assets_workspace_id_content_ref":
            raise
    # A concurrent request created it first; read the winner back.
    async with short_session(storage) as session:
        return _replay((await session.scalars(existing)).one(), body), False


async def get_asset(storage: Storage, actor: Principal, workspace_id: str, asset_id: str) -> Asset:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return Asset.model_validate(await find_row(session, actor, AssetRow, scope, asset_id, "read"))


async def list_assets(
    storage: Storage, actor: Principal, workspace_id: str, *, limit: int, cursor: str | None
) -> AssetPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        rows, next_cursor = await cursors.id_page(
            session,
            select(AssetRow).where(AssetRow.workspace_id == scope.workspace_id),
            AssetRow.id,
            kind="assets",
            owner=scope.workspace_id,
            cursor=cursor,
            limit=limit,
        )
        return AssetPage(items=[Asset.model_validate(row) for row in rows], next_cursor=next_cursor)


async def retire_asset(
    storage: Storage, actor: Principal, workspace_id: str, asset_id: str, *, if_match: str | None
) -> Asset:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, AssetRow, scope, asset_id, "write", lock=True)
        require_match(if_match, row.id, row.version)
        if row.retired_at is None:
            row.retired_at = await now(session)
            audit_row(session, actor, row, "retire")
            await session.flush()
        return Asset.model_validate(row)


async def read_asset_content(
    storage: Storage, objects: ObjectStore, actor: Principal, workspace_id: str, asset_id: str
) -> tuple[Asset, bytes]:
    """Retired assets stay readable to anyone who may read the workspace."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        row = await find_row(session, actor, AssetRow, scope, asset_id, "read")
        reference = ObjectRef(row.content_ref, row.digest, row.size, row.content_type)
        asset = Asset.model_validate(row)
    return asset, await read(objects, reference)


async def require_usable(session: AsyncSession, workspace_id: str, assets: Mapping[str, str]) -> None:
    """New input may name only this workspace's unretired assets; retained history keeps retired ones readable.

    `assets` maps the field that names each asset to its ID.
    """
    if not assets:
        return
    usable = set(
        (
            await session.scalars(
                select(AssetRow.id).where(
                    AssetRow.workspace_id == workspace_id,
                    AssetRow.id.in_(set(assets.values())),
                    AssetRow.retired_at.is_(None),
                )
            )
        ).all()
    )
    for field, asset_id in assets.items():
        if asset_id not in usable:
            raise ServiceError(
                "invalid_argument",
                "Asset is not usable in this workspace",
                {"field": field, "reason": "not_usable", "kind": "asset", "id": asset_id},
            )
