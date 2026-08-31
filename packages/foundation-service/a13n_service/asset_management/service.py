"""Application services for immutable Asset publication and management."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.models import IdempotencyEvidenceRecord, OutboxRecord
from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.iam.models import SecurityAuditRecord, WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction

from .cursors import AssetCursorError, decode_asset_cursor, encode_asset_cursor
from .domain import (
    Asset,
    AssetCollection,
    AssetSourceKind,
    new_asset_id,
    normalize_asset_filename,
    normalize_media_type,
)
from .errors import (
    AssetManagementError,
    asset_content_invalid,
    asset_idempotency_conflict,
    asset_media_type_invalid,
    asset_not_found,
)
from .models import AssetRecord
from .objects import ASSET_OBJECT_DESTINATION, AssetObjectStore
from .staging import AssetStaging, StagedAssetContent

IDEMPOTENCY_LIFETIME = timedelta(hours=24)
_UPLOAD_OPERATION = "asset.upload"
_MAX_IDEMPOTENCY_KEY_BYTES = 512


@dataclass(frozen=True, slots=True)
class AssetUploadResult:
    asset: Asset
    status_code: int


@dataclass(frozen=True, slots=True)
class PreparedAssetContent:
    asset: Asset
    content: StagedAssetContent


@dataclass(frozen=True, slots=True)
class _UploadIdentity:
    key_digest: str
    request_digest: str


class AssetService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        objects: AssetObjectStore,
        staging: AssetStaging,
        *,
        max_size_bytes: int,
        clock=None,
    ) -> None:
        self._sessions = sessions
        self._objects = objects
        self._staging = staging
        self._max_size_bytes = max_size_bytes
        self._clock = clock or (lambda: datetime.now(UTC))

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
    ) -> AssetUploadResult:
        key_digest = _idempotency_key_digest(idempotency_key)
        normalized_filename = _normalize_filename(filename)
        normalized_media_type = _normalize_media_type(media_type)
        organization_id = await self._preauthorize_create(actor=actor, workspace_id=workspace_id)
        staged = await self._staging.stage_upload(
            body,
            max_size_bytes=self._max_size_bytes,
            content_length=content_length,
        )
        try:
            if (
                normalized_media_type != "application/octet-stream"
                and staged.detected_media_type is not None
                and staged.detected_media_type != normalized_media_type
            ):
                raise asset_media_type_invalid()
            identity = _UploadIdentity(
                key_digest=key_digest,
                request_digest=_canonical_upload_digest(
                    workspace_id=workspace_id,
                    filename=normalized_filename,
                    media_type=normalized_media_type,
                    size_bytes=staged.size_bytes,
                    content_sha256=staged.content_sha256,
                ),
            )
            replay = await self._load_authorized_upload_replay(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                identity=identity,
            )
            if replay is not None:
                return AssetUploadResult(replay, 201)

            asset_id = new_asset_id()
            await self._objects.publish(
                asset_id=asset_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                content=staged,
            )
            return await self._commit_upload(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                asset_id=asset_id,
                filename=normalized_filename,
                media_type=normalized_media_type,
                content=staged,
                identity=identity,
            )
        finally:
            await staged.remove()

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
            raise AssetManagementError(
                "invalid_cursor", "The collection cursor is invalid.", status_code=400
            ) from error

        async with transaction(self._sessions) as session:
            workspace = await _authorize_asset_workspace(
                session,
                actor=actor,
                workspace_id=workspace_id,
                action=WorkspaceAction.asset_read,
            )
            query = select(AssetRecord).where(
                AssetRecord.organization_id == workspace.organization_id,
                AssetRecord.workspace_id == workspace_id,
                AssetRecord.deleted_at.is_(None),
                # Run persistence and its authorization join are not part of
                # this service artifact yet. Fail closed for rows whose safe
                # public source projection cannot be authorized.
                AssetRecord.source_kind == "upload",
            )
            if source_kind is not None:
                query = query.where(AssetRecord.source_kind == source_kind.value)
            # Run persistence is not present in the current service artifact. No
            # run-output Assets can exist yet, so a run filter has an empty result.
            if source_run_id is not None:
                query = query.where(AssetRecord.source_kind == "run_output", AssetRecord.id.is_(None))
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
            assets = tuple(record.to_resource() for record in page)
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
        workspace_id = actor.boundary_workspace_id
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
                record.deleted_at = now
                session.add(
                    _asset_audit_record(
                        actor=actor,
                        organization_id=workspace.organization_id,
                        workspace_id=workspace_id,
                        asset_id=asset_id,
                        action="asset.delete",
                        source_kind=record.source_kind,
                        now=now,
                    )
                )
                session.add(
                    OutboxRecord(
                        id=new_object_id("obx"),
                        source_kind="asset",
                        source_id=asset_id,
                        destination_kind="asset_content_cleanup",
                        destination_ref=ASSET_OBJECT_DESTINATION,
                        status="pending",
                        available_at=now,
                        claim_generation=0,
                        lease_expires_at=None,
                        attempt_count=0,
                        created_at=now,
                        updated_at=now,
                        published_at=None,
                        dead_lettered_at=None,
                        last_error_code=None,
                    )
                )
        except AuthorizationError as error:
            await self._record_denied(
                actor=actor,
                workspace_id=workspace_id,
                asset_id=asset_id,
                action="asset.delete",
            )
            raise _authorization_error(error, not_found_code="asset_not_found") from error

    async def _get_active(
        self,
        *,
        actor: AuthenticatedActor,
        asset_id: str,
        action: WorkspaceAction,
    ) -> Asset:
        workspace_id = actor.boundary_workspace_id
        async with transaction(self._sessions) as session:
            workspace = await _authorize_asset_workspace(
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
                    AssetRecord.source_kind == "upload",
                )
            )
            if record is None:
                raise asset_not_found()
            return record.to_resource()

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
            await self._record_denied(
                actor=actor,
                workspace_id=workspace_id,
                asset_id=None,
                action="asset.create",
            )
            raise _authorization_error(error) from error

    async def _load_authorized_upload_replay(
        self,
        *,
        actor: AuthenticatedActor,
        organization_id: str,
        workspace_id: str,
        identity: _UploadIdentity,
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
                return await _load_upload_replay(
                    session,
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    identity=identity,
                    now=self._clock(),
                )
        except AuthorizationError as error:
            await self._record_denied(
                actor=actor,
                workspace_id=workspace_id,
                asset_id=None,
                action="asset.create",
            )
            raise _authorization_error(error) from error

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
        identity: _UploadIdentity,
    ) -> AssetUploadResult:
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
                    replay = await _load_upload_replay(
                        session,
                        actor=actor,
                        organization_id=organization_id,
                        workspace_id=workspace_id,
                        identity=identity,
                        now=now,
                    )
                    if replay is not None:
                        result = AssetUploadResult(replay, 201)
                    else:
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
                            IdempotencyEvidenceRecord(
                                id=new_object_id("idem"),
                                organization_id=organization_id,
                                workspace_id=workspace_id,
                                actor_type=actor.principal.principal_type.value,
                                actor_id=actor.principal.principal_id,
                                operation=_UPLOAD_OPERATION,
                                scope_id=workspace_id,
                                key_digest=identity.key_digest,
                                request_digest=identity.request_digest,
                                result_kind="asset",
                                result_ref=asset_id,
                                created_at=now,
                                expires_at=now + IDEMPOTENCY_LIFETIME,
                            )
                        )
                        session.add(
                            _asset_audit_record(
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
                        result = AssetUploadResult(record.to_resource(), 201)
                candidate_is_authoritative = result.asset.id == asset_id
                return result
            except IntegrityError as error:
                if not _is_idempotency_race(error):
                    raise
                replay = await self._load_authorized_upload_replay(
                    actor=actor,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    identity=identity,
                )
                if replay is None:
                    raise asset_idempotency_conflict() from error
                return AssetUploadResult(replay, 201)
            except AuthorizationError as error:
                await self._record_denied(
                    actor=actor,
                    workspace_id=workspace_id,
                    asset_id=None,
                    action="asset.create",
                )
                raise _authorization_error(error) from error
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
            async with transaction(self._sessions) as session:
                owned = await session.scalar(select(AssetRecord.id).where(AssetRecord.id == asset_id))
            if owned is None:
                await self._objects.delete_candidate(
                    asset_id=asset_id,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                )
        except Exception:
            # Unknown database state must never cause deletion of possibly
            # authoritative bytes. A later orphan reconciler can prove absence.
            return

    async def _record_denied(
        self,
        *,
        actor: AuthenticatedActor,
        workspace_id: str,
        asset_id: str | None,
        action: str,
    ) -> None:
        try:
            async with transaction(self._sessions) as session:
                organization_id = await session.scalar(
                    select(WorkspaceRecord.organization_id).where(WorkspaceRecord.id == workspace_id)
                )
                session.add(
                    _asset_audit_record(
                        actor=actor,
                        organization_id=organization_id,
                        workspace_id=workspace_id,
                        asset_id=asset_id,
                        action=action,
                        source_kind="upload",
                        now=self._clock(),
                        outcome="failure",
                    )
                )
        except Exception:
            return


async def _load_upload_replay(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    identity: _UploadIdentity,
    now: datetime,
) -> Asset | None:
    evidence = await session.scalar(
        select(IdempotencyEvidenceRecord).where(
            IdempotencyEvidenceRecord.organization_id == organization_id,
            IdempotencyEvidenceRecord.workspace_id == workspace_id,
            IdempotencyEvidenceRecord.actor_type == actor.principal.principal_type.value,
            IdempotencyEvidenceRecord.actor_id == actor.principal.principal_id,
            IdempotencyEvidenceRecord.operation == _UPLOAD_OPERATION,
            IdempotencyEvidenceRecord.scope_id == workspace_id,
            IdempotencyEvidenceRecord.key_digest == identity.key_digest,
        )
    )
    if evidence is None:
        return None
    if _as_utc(evidence.expires_at) <= _as_utc(now):
        await session.delete(evidence)
        await session.flush()
        return None
    if evidence.request_digest != identity.request_digest:
        raise asset_idempotency_conflict()
    if evidence.result_kind != "asset":
        raise asset_idempotency_conflict()
    record = await session.scalar(
        select(AssetRecord).where(
            AssetRecord.id == evidence.result_ref,
            AssetRecord.organization_id == organization_id,
            AssetRecord.workspace_id == workspace_id,
            AssetRecord.source_kind == "upload",
        )
    )
    if record is None:
        raise asset_idempotency_conflict()
    return record.to_resource()


async def _authorize_asset_workspace(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
    not_found_code: str = "resource_not_found",
):
    try:
        return await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        raise _authorization_error(error, not_found_code=not_found_code) from error


def _authorization_error(
    error: AuthorizationError, *, not_found_code: str = "resource_not_found"
) -> AssetManagementError:
    return AssetManagementError(
        not_found_code if error.concealed else "permission_denied",
        "The requested resource was not found." if error.concealed else "Permission denied.",
        status_code=404 if error.concealed else 403,
    )


def _asset_audit_record(
    *,
    actor: AuthenticatedActor,
    organization_id: str | None,
    workspace_id: str,
    asset_id: str | None,
    action: str,
    source_kind: str,
    now: datetime,
    outcome: Literal["success", "failure"] = "success",
) -> SecurityAuditRecord:
    return SecurityAuditRecord(
        id=new_object_id("aud"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        action=action,
        resource_type="asset",
        resource_id=asset_id,
        auth_method=actor.auth_method,
        credential_id=actor.credential_id,
        outcome=outcome,
        occurred_at=now,
        request_id=actor.request_id,
        details={"source_kind": source_kind},
    )


def _idempotency_key_digest(key: str) -> str:
    try:
        encoded = key.encode("ascii")
    except UnicodeEncodeError as error:
        raise _invalid_idempotency_key() from error
    if not 1 <= len(encoded) <= _MAX_IDEMPOTENCY_KEY_BYTES or any(byte < 0x21 or byte > 0x7E for byte in encoded):
        raise _invalid_idempotency_key()
    return hashlib.sha256(encoded).hexdigest()


def _invalid_idempotency_key() -> AssetManagementError:
    return AssetManagementError(
        "invalid_request",
        "Idempotency-Key must contain 1 through 512 visible ASCII bytes.",
        status_code=400,
    )


def _normalize_filename(value: str) -> str:
    try:
        return normalize_asset_filename(value)
    except ValueError as error:
        raise asset_content_invalid() from error


def _normalize_media_type(value: str | None) -> str:
    try:
        return normalize_media_type(value)
    except ValueError as error:
        raise asset_media_type_invalid() from error


def _canonical_upload_digest(
    *,
    workspace_id: str,
    filename: str,
    media_type: str,
    size_bytes: int,
    content_sha256: str,
) -> str:
    encoded = json.dumps(
        {
            "content_sha256": content_sha256,
            "filename": filename,
            "media_type": media_type,
            "size_bytes": size_bytes,
            "workspace_id": workspace_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _is_idempotency_race(error: IntegrityError) -> bool:
    diagnostic = getattr(getattr(error, "orig", None), "diag", None)
    if diagnostic is not None:
        return getattr(diagnostic, "constraint_name", None) == "uq_idempotency_evidence_replay_scope"
    message = str(error.orig).casefold()
    return "unique constraint failed" in message and "idempotency_evidence.actor_type" in message


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
