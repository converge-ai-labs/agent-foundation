"""Application services for immutable Asset publication and management."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.interactions.models import RunAttemptRecord
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .audit import record_denied
from .cursors import AssetCursorError, decode_asset_cursor, encode_asset_cursor
from .deletion import tombstone_asset
from .domain import (
    Asset,
    AssetCollection,
    AssetSourceKind,
)
from .errors import (
    AssetError,
    asset_content_invalid,
    asset_not_found,
)
from .models import AssetRecord
from .objects import AssetObjectStore
from .persistence import (
    authorization_error,
    authorize_asset_workspace,
)
from .provenance import project_assets, require_readable_run
from .staging import StagedAssetContent


@dataclass(frozen=True, slots=True)
class PreparedAssetContent:
    asset: Asset
    content: StagedAssetContent


class AssetCatalog:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], objects: AssetObjectStore, *, clock: Clock = utc_now
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._clock = clock

    async def list(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        limit: int,
        cursor: str | None,
        source_kind: AssetSourceKind | None,
        source_run_id: str | None,
    ) -> AssetCollection:
        scope = {
            "workspace_id": workspace_id,
            "source_kind": source_kind.value if source_kind is not None else None,
            "source_run_id": source_run_id,
        }
        try:
            after = decode_asset_cursor(cursor, scope=scope) if cursor is not None else None
        except AssetCursorError as error:
            raise AssetError(
                "invalid_cursor", "The collection cursor is invalid.", category=ErrorCategory.invalid_request
            ) from error

        async with transaction(self._sessions) as session:
            workspace = await authorize_asset_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.asset_read,
            )
            query = select(AssetRecord).where(
                AssetRecord.organization_id == workspace.organization_id,
                AssetRecord.workspace_id == workspace_id,
                AssetRecord.deleted_at.is_(None),
            )
            if source_kind is not None:
                query = query.where(AssetRecord.source_kind == source_kind.value)
            if source_run_id is not None:
                await require_readable_run(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    run_id=source_run_id,
                )
                query = query.where(
                    AssetRecord.source_run_attempt_id.in_(
                        select(RunAttemptRecord.id).where(
                            RunAttemptRecord.run_id == source_run_id,
                            RunAttemptRecord.organization_id == workspace.organization_id,
                        )
                    )
                )
            if after is not None:
                created_at, asset_id = after
                query = query.where(
                    or_(
                        AssetRecord.created_at < created_at,
                        and_(AssetRecord.created_at == created_at, AssetRecord.id < asset_id),
                    )
                )
            records = tuple(
                (
                    await session.scalars(
                        query.order_by(AssetRecord.created_at.desc(), AssetRecord.id.desc()).limit(limit + 1)
                    )
                ).all()
            )
            page = records[:limit]
            assets = await project_assets(
                session,
                actor=actor,
                workspace_id=workspace_id,
                records=page,
            )
            next_cursor = None
            if len(records) > limit and page:
                next_cursor = encode_asset_cursor(
                    created_at=page[-1].created_at,
                    asset_id=page[-1].id,
                    scope=scope,
                )
            return AssetCollection(items=assets, next_cursor=next_cursor)

    async def get(self, *, actor: AuthenticatedActor, asset_id: str) -> Asset:
        return await self._get_active(actor=actor, asset_id=asset_id, action=WorkspaceAction.asset_read)

    async def require_for_use(self, *, actor: AuthenticatedActor, asset_id: str) -> Asset:
        """Authorize one exact active Asset for a future Run-acceptance boundary."""

        return await self._get_active(actor=actor, asset_id=asset_id, action=WorkspaceAction.asset_use)

    async def prepare_content(self, *, actor: AuthenticatedActor, asset_id: str) -> PreparedAssetContent:
        asset = await self.get(actor=actor, asset_id=asset_id)
        content = await self._objects.prepare_verified_content(asset)
        try:
            # Close the delete race before bytes cross the HTTP delivery boundary.
            active = await self.get(actor=actor, asset_id=asset_id)
            if active != asset:
                raise asset_content_invalid()
            return PreparedAssetContent(asset=asset, content=content)
        except BaseException:
            await content.remove()
            raise

    async def delete(self, *, actor: AuthenticatedActor, asset_id: str) -> None:
        workspace_id = actor.workspace_id
        now = self._clock()
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.asset_delete,
                )
                record = await session.scalar(
                    select(AssetRecord)
                    .where(
                        AssetRecord.id == asset_id,
                        AssetRecord.organization_id == workspace.organization_id,
                        AssetRecord.workspace_id == workspace_id,
                        AssetRecord.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
                if record is None:
                    raise asset_not_found()
                tombstone_asset(session, record, actor=actor, now=now)
        except AuthorizationError as error:
            await record_denied(
                self._sessions,
                clock=self._clock,
                actor=actor,
                workspace_id=workspace_id,
                asset_id=asset_id,
                action="asset.delete",
                source_kind=None,
            )
            raise authorization_error(error, not_found_code="asset_not_found") from error

    async def _get_active(
        self,
        *,
        actor: AuthenticatedActor,
        asset_id: str,
        action: WorkspaceAction,
    ) -> Asset:
        workspace_id = actor.workspace_id
        async with transaction(self._sessions) as session:
            workspace = await authorize_asset_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=action,
                not_found_code="asset_not_found",
            )
            record = await session.scalar(
                select(AssetRecord).where(
                    AssetRecord.id == asset_id,
                    AssetRecord.organization_id == workspace.organization_id,
                    AssetRecord.workspace_id == workspace_id,
                    AssetRecord.deleted_at.is_(None),
                )
            )
            if record is None:
                raise asset_not_found()
            return (
                await project_assets(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    records=(record,),
                )
            )[0]
