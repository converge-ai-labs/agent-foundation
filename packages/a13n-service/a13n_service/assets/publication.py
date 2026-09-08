"""Explicit Environment-to-Asset publication under a live Attempt fence."""

import posixpath
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass
from datetime import datetime

from a13n_harness import AgentContext
from pydantic_ai import RunContext, ToolFailed
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_agent,
    authorize_workspace,
)
from a13n_service.iam.audit import security_audit_record
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.ids import new_object_id
from a13n_service.interactions.attempts import AttemptContext, lock_attempt_authority, read_attempt_authority
from a13n_service.interactions.models import RunRecord, SessionRecord
from a13n_service.object_retention.persistence import require_object_publications
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import utc_now

from .domain import Asset, AssetRef, new_asset_id, normalize_asset_filename, normalize_media_type
from .errors import asset_content_invalid, asset_idempotency_conflict, asset_media_type_invalid
from .models import AssetRecord
from .objects import AssetObjectStore, asset_content_key
from .staging import AssetStaging, StagedAssetContent


@dataclass(frozen=True)
class AssetPublicationScope:
    current_attempt: Callable[[], AttemptContext]
    workspace_id: str
    agent_id: str


class AgentAssetPublisher:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        objects: AssetObjectStore,
        staging: AssetStaging,
        *,
        max_size_bytes: int,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._staging = staging
        self._max_size_bytes = max_size_bytes
        self._clock = clock

    async def publish(
        self,
        scope: AssetPublicationScope,
        *,
        invocation_id: str,
        filename: str,
        media_type: str | None,
        body: AsyncIterable[bytes],
    ) -> AssetRef:
        try:
            return await self._publish(
                scope, invocation_id=invocation_id, filename=filename, media_type=media_type, body=body
            )
        except AuthorizationError:
            await self._record_denied(scope)
            raise

    async def _publish(
        self,
        scope: AssetPublicationScope,
        *,
        invocation_id: str,
        filename: str,
        media_type: str | None,
        body: AsyncIterable[bytes],
    ) -> AssetRef:
        filename = normalize_asset_filename(filename)
        media_type = normalize_media_type(media_type)
        async with short_session(self._sessions) as database:
            await self._authorize(database, scope, lock=False)
        staged = await self._staging.stage_upload(body, max_size_bytes=self._max_size_bytes, content_length=None)
        asset_id = new_asset_id()
        attempt = scope.current_attempt()
        try:
            if media_type != "application/octet-stream" and staged.detected_media_type not in (None, media_type):
                raise asset_media_type_invalid()
            async with short_session(self._sessions) as database:
                await self._authorize(database, scope, lock=False)
                replay = await self._replay(database, scope, invocation_id, filename, media_type, staged)
            if replay is not None:
                return AssetRef.from_asset(replay)
            await self._objects.publish(
                asset_id=asset_id,
                organization_id=attempt.organization_id,
                workspace_id=scope.workspace_id,
                content=staged,
            )
            async with transaction(self._sessions) as database:
                actor = await self._authorize(database, scope, lock=True)
                # The Attempt row serializes concurrent reconciliations of this invocation.
                replay = await self._replay(database, scope, invocation_id, filename, media_type, staged)
                if replay is not None:
                    return AssetRef.from_asset(replay)
                await require_object_publications(
                    database,
                    (
                        asset_content_key(
                            organization_id=attempt.organization_id, workspace_id=scope.workspace_id, asset_id=asset_id
                        ),
                    ),
                )
                record = AssetRecord(
                    id=asset_id,
                    organization_id=attempt.organization_id,
                    workspace_id=scope.workspace_id,
                    filename=filename,
                    media_type=media_type,
                    size_bytes=staged.size_bytes,
                    content_sha256=staged.content_sha256,
                    source_kind="run_output",
                    source_principal_type=None,
                    source_principal_id=None,
                    source_run_attempt_id=attempt.run_attempt_id,
                    source_invocation_id=invocation_id,
                    created_at=self._clock(),
                    deleted_at=None,
                )
                database.add(record)
                database.add(
                    security_audit_record(
                        audit_id=new_object_id("aud"),
                        actor=actor,
                        organization_id=attempt.organization_id,
                        workspace_id=scope.workspace_id,
                        action="asset.create",
                        resource_type="asset",
                        resource_id=asset_id,
                        outcome="success",
                        occurred_at=self._clock(),
                        details={
                            "source_kind": "run_output",
                            "run_id": attempt.run_id,
                            "run_attempt_id": attempt.run_attempt_id,
                        },
                    )
                )
                await database.flush()
                result = record.to_resource(source_run_id=attempt.run_id)
            return AssetRef.from_asset(result)
        finally:
            await staged.remove()
            # Ownership is rechecked even when commit acknowledgement was lost.
            await self._objects.delete_candidate(
                sessions=self._sessions,
                asset_id=asset_id,
                organization_id=attempt.organization_id,
                workspace_id=scope.workspace_id,
            )

    async def _record_denied(self, scope: AssetPublicationScope) -> None:
        attempt = scope.current_attempt()
        try:
            async with transaction(self._sessions) as database:
                run = await database.get(RunRecord, attempt.run_id)
                if run is None or run.organization_id != attempt.organization_id:
                    return
                database.add(
                    security_audit_record(
                        audit_id=new_object_id("aud"),
                        actor=AuthenticatedActor(
                            principal=PrincipalRef(
                                principal_type=PrincipalType(run.authority_principal_type),
                                principal_id=run.authority_principal_id,
                            ),
                            auth_method="internal",
                            credential_id="attempt-assets",
                            boundary_workspace_id=scope.workspace_id,
                        ),
                        organization_id=attempt.organization_id,
                        workspace_id=scope.workspace_id,
                        action="asset.create",
                        resource_type="asset",
                        resource_id=None,
                        outcome="failure",
                        occurred_at=self._clock(),
                        details={
                            "source_kind": "run_output",
                            "run_id": attempt.run_id,
                            "run_attempt_id": attempt.run_attempt_id,
                        },
                    )
                )
        except Exception:
            # Denial must remain a denial if best-effort audit storage is unavailable.
            return

    async def _authorize(
        self, database: AsyncSession, scope: AssetPublicationScope, *, lock: bool
    ) -> AuthenticatedActor:
        check = lock_attempt_authority if lock else read_attempt_authority
        run, _, _ = await check(database, scope.current_attempt(), self._clock())
        session = await database.get(SessionRecord, run.session_id)
        if (
            session is None
            or session.organization_id != run.organization_id
            or session.workspace_id != scope.workspace_id
        ):
            raise asset_content_invalid()
        actor = AuthenticatedActor(
            principal=PrincipalRef(
                principal_type=PrincipalType(run.authority_principal_type), principal_id=run.authority_principal_id
            ),
            auth_method="internal",
            credential_id="attempt-assets",
            boundary_workspace_id=scope.workspace_id,
        )
        for agent_id in {run.agent_id, scope.agent_id}:
            await authorize_agent(
                database,
                actor=actor,
                workspace_id=scope.workspace_id,
                agent_id=agent_id,
                action=WorkspaceAction.agent_invoke,
            )
        await authorize_workspace(
            database, actor=actor, workspace_id=scope.workspace_id, action=WorkspaceAction.asset_create
        )
        return actor

    async def _replay(
        self,
        database: AsyncSession,
        scope: AssetPublicationScope,
        invocation_id: str,
        filename: str,
        media_type: str,
        staged: StagedAssetContent,
    ) -> Asset | None:
        attempt = scope.current_attempt()
        row = await database.scalar(
            select(AssetRecord).where(
                AssetRecord.organization_id == attempt.organization_id,
                AssetRecord.workspace_id == scope.workspace_id,
                AssetRecord.source_run_attempt_id == attempt.run_attempt_id,
                AssetRecord.source_invocation_id == invocation_id,
            )
        )
        if row is None:
            return None
        if (row.filename, row.media_type, row.size_bytes, row.content_sha256) != (
            filename,
            media_type,
            staged.size_bytes,
            staged.content_sha256,
        ):
            raise asset_idempotency_conflict()
        return row.to_resource(source_run_id=attempt.run_id)


class AssetCapability(AbstractCapability[AgentContext]):
    id = "a13n.foundation.assets"

    def __init__(self, publisher: AgentAssetPublisher, scope: AssetPublicationScope) -> None:
        self._publisher = publisher
        self._scope = scope

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset([self.publish_asset], id=self.id)

    async def publish_asset(
        self,
        ctx: RunContext[AgentContext],
        path: str,
        filename: str | None = None,
        media_type: str | None = None,
    ) -> AssetRef:
        """Publish a regular file from the default Environment as a durable Asset."""
        environment = ctx.deps.environment
        if environment is None:
            raise ToolFailed("Asset publication requires a readable default Environment.")
        try:

            async def chunks():
                selection = await environment.resolve_files(path)
                async with environment.open_files(selection) as files:
                    metadata = await files.stat(selection.logical_path)
                    if metadata.kind != "file":
                        raise asset_content_invalid()
                    async for chunk in files.read_bytes_stream(selection.logical_path):
                        yield chunk

            return await self._publisher.publish(
                self._scope,
                invocation_id=new_object_id("tool"),
                filename=filename if filename is not None else posixpath.basename(path),
                media_type=media_type,
                body=chunks(),
            )
        except Exception as error:
            raise ToolFailed("The selected file could not be published as an Asset.") from error
