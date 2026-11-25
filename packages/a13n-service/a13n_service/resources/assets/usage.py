"""Workspace usage for tracked asset and upload bytes."""

from sqlalchemy import func, literal, select, union_all

from a13n_service.infra.db import Storage, short_session
from a13n_service.resources.assets.schemas import CreatorStorage, WorkspaceStorage
from a13n_service.resources.assets.tables import AssetRow
from a13n_service.resources.uploads.service import object_key
from a13n_service.resources.uploads.tables import UploadRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.tables import PrincipalRow


async def get_workspace_storage(storage: Storage, actor: Principal, workspace_id: str) -> WorkspaceStorage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        assets = (
            select(
                AssetRow.created_by_id,
                func.sum(AssetRow.size).filter(AssetRow.retired_at.is_(None)).label("active"),
                func.sum(AssetRow.size).filter(AssetRow.retired_at.is_not(None)).label("retired"),
                literal(0).label("staged"),
            )
            .where(AssetRow.workspace_id == scope.workspace_id)
            .group_by(AssetRow.created_by_id)
        )
        # Both branches share a statement snapshot when uploads become assets. Retirement keeps the reference.
        referenced = select(1).where(
            AssetRow.workspace_id == scope.workspace_id,
            AssetRow.content_ref == literal(object_key(scope.organization_id, "")) + UploadRow.id,
        )
        staged = (
            select(UploadRow.created_by_id, literal(0), literal(0), func.sum(UploadRow.size))
            .where(UploadRow.workspace_id == scope.workspace_id, ~referenced.exists())
            .group_by(UploadRow.created_by_id)
        )
        usage = union_all(assets, staged).subquery()
        by_creator = (
            select(usage.c.created_by_id, func.sum(usage.c.active), func.sum(usage.c.retired), func.sum(usage.c.staged))
            .group_by(usage.c.created_by_id)
            .subquery()
        )
        rows = await session.execute(
            select(by_creator, PrincipalRow.name)
            .join(PrincipalRow, PrincipalRow.id == by_creator.c.created_by_id)
            .order_by(PrincipalRow.name, PrincipalRow.id)
        )
        creators = []
        for creator_id, active, retired, staged_bytes, name in rows:
            active, retired, staged_bytes = (int(value or 0) for value in (active, retired, staged_bytes))
            creators.append(
                CreatorStorage(
                    created_by_id=creator_id,
                    creator_name=name,
                    active_bytes=active,
                    retired_bytes=retired,
                    staged_upload_bytes=staged_bytes,
                    total_bytes=active + retired + staged_bytes,
                )
            )
        return WorkspaceStorage(
            active_bytes=sum(creator.active_bytes for creator in creators),
            retired_bytes=sum(creator.retired_bytes for creator in creators),
            staged_upload_bytes=sum(creator.staged_upload_bytes for creator in creators),
            total_bytes=sum(creator.total_bytes for creator in creators),
            by_creator=creators,
        )
