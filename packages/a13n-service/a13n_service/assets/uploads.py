"""Application services for immutable Asset publication and management."""

from __future__ import annotations

from collections.abc import AsyncIterable
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.idempotency import (
    EvidenceScope,
    IdempotencyIdentity,
    is_evidence_unique_race,
    new_evidence,
)
from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.object_retention.persistence import require_object_publications
from a13n_service.storage import transaction
from a13n_service.temporal import Clock, utc_now

from .audit import asset_audit_record, record_denied
from .domain import (
    Asset,
    AssetSourceKind,
    UploadedAssetSource,
)
from .errors import (
    asset_content_invalid,
    asset_idempotency_conflict,
)
from .models import AssetRecord
from .objects import AssetObjectStore, asset_content_key
from .persistence import (
    UPLOAD_OPERATION,
    authorization_error,
    canonical_upload_digest,
    idempotency_key_digest,
    load_upload_replay,
)
from .publication import AssetPublisher
from .staging import AssetStaging, StagedAssetContent


@dataclass(frozen=True, slots=True)
class PreparedAssetPublication:
    """Published object candidate awaiting an owning application transaction."""

    asset: Asset


class AssetUploadService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        objects: AssetObjectStore,
        staging: AssetStaging,
        *,
        max_size_bytes: int,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._publisher = AssetPublisher(objects, staging, max_size_bytes=max_size_bytes, clock=clock)
        self._clock = clock

    async def upload(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        idempotency_key: str,
        filename: str,
        media_type: str | None,
        body: AsyncIterable[bytes],
        content_length: int | None,
    ) -> Asset:
        key_digest = idempotency_key_digest(idempotency_key)
        organization_id = await self._preauthorize_create(actor=actor, workspace_id=workspace_id)
        async with self._publisher.stage(
            organization_id=organization_id,
            workspace_id=workspace_id,
            source=UploadedAssetSource(principal=actor.principal),
            filename=filename,
            media_type=media_type,
            body=body,
            content_length=content_length,
        ) as candidate:
            asset = candidate.asset
            identity = IdempotencyIdentity(
                key_digest=key_digest,
                request_digest=canonical_upload_digest(
                    workspace_id=workspace_id,
                    filename=asset.filename,
                    media_type=asset.media_type,
                    size_bytes=asset.size_bytes,
                    content_sha256=asset.content_sha256,
                ),
            )
            replay = await self._load_authorized_upload_replay(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                identity=identity,
            )
            if replay is not None:
                return replay

            await self._publisher.publish(candidate)
            return await self._commit_upload(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                asset_id=asset.id,
                filename=asset.filename,
                media_type=asset.media_type,
                content=candidate.content,
                identity=identity,
            )

    async def prepare_protocol_import(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        filename: str,
        media_type: str | None,
        body: AsyncIterable[bytes],
        content_length: int | None,
    ) -> PreparedAssetPublication:
        """Publish one bounded candidate without committing relational authority."""

        organization_id = await self._preauthorize_protocol_import(actor=actor, workspace_id=workspace_id)
        async with self._publisher.stage(
            organization_id=organization_id,
            workspace_id=workspace_id,
            source=UploadedAssetSource(principal=actor.principal),
            filename=filename,
            media_type=media_type,
            body=body,
            content_length=content_length,
        ) as candidate:
            await self._publisher.publish(candidate)
            return PreparedAssetPublication(asset=candidate.asset)

    async def commit_protocol_imports_in_transaction(
        self,
        session: AsyncSession,
        *,
        actor: AuthenticatedActor,
        publications: tuple[PreparedAssetPublication, ...],
    ) -> None:
        """Make prepared protocol imports authoritative in their owning transaction."""

        if not publications:
            return
        workspace_id = actor.workspace_id
        workspace = await authorize_workspace(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.asset_create,
        )
        await authorize_workspace(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.asset_use,
        )
        if len({publication.asset.id for publication in publications}) != len(publications):
            raise asset_content_invalid()
        await require_object_publications(
            session,
            (
                asset_content_key(
                    organization_id=publication.asset.organization_id,
                    workspace_id=publication.asset.workspace_id,
                    asset_id=publication.asset.id,
                )
                for publication in publications
            ),
        )
        now = self._clock()
        for publication in publications:
            asset = publication.asset
            if (
                asset.organization_id != workspace.organization_id
                or asset.workspace_id != workspace_id
                or asset.deleted_at is not None
                or asset.source.kind != AssetSourceKind.upload
                or asset.source.principal != actor.principal
            ):
                raise asset_content_invalid()
            session.add(
                AssetRecord(
                    id=asset.id,
                    organization_id=asset.organization_id,
                    workspace_id=asset.workspace_id,
                    filename=asset.filename,
                    media_type=asset.media_type,
                    size_bytes=asset.size_bytes,
                    content_sha256=asset.content_sha256,
                    source_kind="upload",
                    source_principal_type=actor.principal.principal_type.value,
                    source_principal_id=actor.principal.principal_id,
                    source_run_attempt_id=None,
                    source_invocation_id=None,
                    created_at=asset.created_at,
                    deleted_at=None,
                )
            )
            session.add(
                asset_audit_record(
                    actor=actor,
                    organization_id=asset.organization_id,
                    workspace_id=asset.workspace_id,
                    asset_id=asset.id,
                    action="asset.create",
                    source_kind="upload",
                    now=now,
                )
            )
        await session.flush()

    async def discard_protocol_import(self, publication: PreparedAssetPublication) -> None:
        asset = publication.asset
        await self._delete_candidate_if_unowned(
            asset_id=asset.id,
            organization_id=asset.organization_id,
            workspace_id=asset.workspace_id,
        )

    async def _preauthorize_create(self, *, actor: AuthenticatedActor, workspace_id: str) -> str:
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.asset_create,
                )
                return workspace.organization_id
        except AuthorizationError as error:
            await record_denied(
                self._sessions,
                clock=self._clock,
                actor=actor,
                workspace_id=workspace_id,
                asset_id=None,
                action="asset.create",
            )
            raise authorization_error(error) from error

    async def _preauthorize_protocol_import(self, *, actor: AuthenticatedActor, workspace_id: str) -> str:
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.asset_create,
                )
                await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.asset_use,
                )
                return workspace.organization_id
        except AuthorizationError as error:
            await record_denied(
                self._sessions,
                clock=self._clock,
                actor=actor,
                workspace_id=workspace_id,
                asset_id=None,
                action="asset.create",
            )
            raise authorization_error(error) from error

    async def _load_authorized_upload_replay(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        identity: IdempotencyIdentity,
    ) -> Asset | None:
        try:
            async with transaction(self._sessions) as session:
                workspace = await authorize_workspace(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    action=WorkspaceAction.asset_create,
                )
                if workspace.organization_id != organization_id:
                    raise AuthorizationError("workspace_owner_changed", concealed=True)
                return await load_upload_replay(
                    session,
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    identity=identity,
                    now=self._clock(),
                )
        except AuthorizationError as error:
            await record_denied(
                self._sessions,
                clock=self._clock,
                actor=actor,
                workspace_id=workspace_id,
                asset_id=None,
                action="asset.create",
            )
            raise authorization_error(error) from error

    async def _commit_upload(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        asset_id: str,
        filename: str,
        media_type: str,
        content: StagedAssetContent,
        identity: IdempotencyIdentity,
    ) -> Asset:
        now = self._clock()
        candidate_is_authoritative = False
        try:
            try:
                async with transaction(self._sessions) as session:
                    workspace = await authorize_workspace(
                        session,
                        actor=actor,
                        workspace_id=workspace_id,
                        action=WorkspaceAction.asset_create,
                    )
                    if workspace.organization_id != organization_id:
                        raise AuthorizationError("workspace_owner_changed", concealed=True)
                    replay = await load_upload_replay(
                        session,
                        actor=actor,
                        organization_id=organization_id,
                        workspace_id=workspace_id,
                        identity=identity,
                        now=now,
                    )
                    if replay is not None:
                        result = replay
                    else:
                        await require_object_publications(
                            session,
                            (
                                asset_content_key(
                                    organization_id=organization_id, workspace_id=workspace_id, asset_id=asset_id
                                ),
                            ),
                        )
                        record = AssetRecord(
                            id=asset_id,
                            organization_id=organization_id,
                            workspace_id=workspace_id,
                            filename=filename,
                            media_type=media_type,
                            size_bytes=content.size_bytes,
                            content_sha256=content.content_sha256,
                            source_kind="upload",
                            source_principal_type=actor.principal.principal_type.value,
                            source_principal_id=actor.principal.principal_id,
                            source_run_attempt_id=None,
                            source_invocation_id=None,
                            created_at=now,
                            deleted_at=None,
                        )
                        session.add(record)
                        session.add(
                            new_evidence(
                                organization_id=organization_id,
                                scope=EvidenceScope(
                                    workspace_id=workspace_id,
                                    actor_type=actor.principal.principal_type.value,
                                    actor_id=actor.principal.principal_id,
                                    operation=UPLOAD_OPERATION,
                                    scope_id=workspace_id,
                                    organization_id=actor.boundary_organization_id,
                                ),
                                identity=identity,
                                result_kind="asset",
                                result_ref=asset_id,
                                now=now,
                            )
                        )
                        session.add(
                            asset_audit_record(
                                actor=actor,
                                organization_id=organization_id,
                                workspace_id=workspace_id,
                                asset_id=asset_id,
                                action="asset.create",
                                source_kind="upload",
                                now=now,
                            )
                        )
                        await session.flush()
                        result = record.to_resource()
                candidate_is_authoritative = result.id == asset_id
                return result
            except IntegrityError as error:
                if not is_evidence_unique_race(error):
                    raise
                replay = await self._load_authorized_upload_replay(
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    identity=identity,
                )
                if replay is None:
                    raise asset_idempotency_conflict() from error
                return replay
            except AuthorizationError as error:
                await record_denied(
                    self._sessions,
                    clock=self._clock,
                    actor=actor,
                    workspace_id=workspace_id,
                    asset_id=None,
                    action="asset.create",
                )
                raise authorization_error(error) from error
        finally:
            if not candidate_is_authoritative:
                await self._delete_candidate_if_unowned(
                    asset_id=asset_id,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                )

    async def _delete_candidate_if_unowned(
        self,
        *,
        asset_id: str,
        organization_id: str,
        workspace_id: str,
    ) -> None:
        try:
            await self._objects.delete_candidate(
                sessions=self._sessions,
                asset_id=asset_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
        except Exception:
            # Unknown session state must never cause deletion of possibly
            # authoritative bytes. A later orphan reconciler can prove absence.
            return
