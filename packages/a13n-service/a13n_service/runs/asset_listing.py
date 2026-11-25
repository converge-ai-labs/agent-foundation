"""Workspace asset collection with provenance resolved from execution records."""

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.http import PageLimit
from a13n_service.infra.ids import ObjectId
from a13n_service.resources.assets.schemas import Asset, AssetPage, AssetStatus, ListedAsset, SourceKind
from a13n_service.resources.assets.tables import AssetRow
from a13n_service.resources.requests import CurrentRuntime
from a13n_service.runs.tables import RunRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal
from a13n_service.tenancy.requests import Actor, WorkspaceId
from a13n_service.tenancy.tables import PrincipalRow

router = APIRouter(prefix="/api/v1/assets", tags=["assets"])


@router.get("", response_model=AssetPage, openapi_extra={"x-a13n-mcp": True})
async def list_assets(
    workspace_id: WorkspaceId,
    actor: Actor,
    runtime: CurrentRuntime,
    limit: PageLimit = 50,
    cursor: str | None = None,
    query: Annotated[
        str | None, Query(max_length=255, description="Case-insensitive literal filename substring.")
    ] = None,
    created_by_id: ObjectId | None = None,
    source_kind: SourceKind | None = None,
    status: AssetStatus = "all",
) -> AssetPage:
    return await _list_assets(
        runtime.storage,
        actor,
        workspace_id,
        limit=limit,
        cursor=cursor,
        query=query,
        created_by_id=created_by_id,
        source_kind=source_kind,
        status=status,
    )


async def _list_assets(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    *,
    limit: int,
    cursor: str | None,
    query: str | None = None,
    created_by_id: str | None = None,
    source_kind: SourceKind | None = None,
    status: AssetStatus = "all",
) -> AssetPage:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        matching = select(AssetRow).where(AssetRow.workspace_id == scope.workspace_id)
        if query:
            matching = matching.where(AssetRow.name.icontains(query, autoescape=True))
        if created_by_id is not None:
            matching = matching.where(AssetRow.created_by_id == created_by_id)
        if source_kind is not None:
            matching = matching.where(
                AssetRow.source.is_(None) if source_kind == "user_upload" else AssetRow.source.is_not(None)
            )
        if status != "all":
            matching = matching.where(
                AssetRow.retired_at.is_(None) if status == "active" else AssetRow.retired_at.is_not(None)
            )
        owner = scope.workspace_id
        if query or created_by_id is not None or source_kind is not None or status != "all":
            owner = cursors.query_owner(owner, query or None, created_by_id, source_kind, status)
        rows, next_cursor = await cursors.id_page(
            session,
            matching,
            AssetRow.id,
            kind="assets",
            owner=owner,
            cursor=cursor,
            limit=limit,
        )
        total_count = await session.scalar(matching.with_only_columns(func.count(AssetRow.id))) or 0
        items = []
        if rows:
            # Resolve provenance for this page only, including assets published before Files existed.
            attribution = await session.execute(
                select(AssetRow.id, PrincipalRow.name, RunRow.thread_id, RunRow.session_id)
                .join(PrincipalRow, PrincipalRow.id == AssetRow.created_by_id)
                .outerjoin(
                    RunRow,
                    (RunRow.workspace_id == AssetRow.workspace_id) & (RunRow.id == AssetRow.source["run_id"].astext),
                )
                .where(AssetRow.workspace_id == scope.workspace_id, AssetRow.id.in_([row.id for row in rows]))
            )
            by_id = {asset_id: (name, thread_id, session_id) for asset_id, name, thread_id, session_id in attribution}
            for row in rows:
                creator_name, thread_id, session_id = by_id[row.id]
                items.append(
                    ListedAsset(
                        **Asset.model_validate(row).model_dump(),
                        creator_name=creator_name,
                        thread_id=thread_id,
                        session_id=session_id,
                    )
                )
        return AssetPage(items=items, next_cursor=next_cursor, total_count=total_count)
