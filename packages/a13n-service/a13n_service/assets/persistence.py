"""Asset publication records, replay evidence, and persistence-boundary rules."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.digests import digest_request
from a13n_service.durable_operations.entity_keys import entity_key, find_by_key
from a13n_service.durable_operations.idempotency import (
    IdempotencyIdentity,
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
)
from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_workspace,
)

from .audit import asset_audit_record
from .domain import (
    Asset,
    UploadedAssetSource,
)
from .errors import (
    AssetError,
)
from .models import AssetRecord

UPLOAD_OPERATION = "asset.upload"


def add_asset_publication(
    session: AsyncSession,
    *,
    asset: Asset,
    actor: AuthenticatedActor,
    now: datetime,
    run_attempt_id: str | None = None,
    invocation_id: str | None = None,
    request_key: str | None = None,
) -> None:
    """Add the immutable candidate and success audit to the caller's transaction.

    The caller owns authorization, replay decisions, object-publication locks,
    and flush/commit. Run provenance comes only from the trusted invocation.
    """
    if isinstance(asset.source, UploadedAssetSource):
        principal, run_id = asset.source.principal, None
    else:
        principal, run_id = None, asset.source.run_id
    session.add(
        AssetRecord(
            request_key=request_key,
            id=asset.id,
            organization_id=asset.organization_id,
            workspace_id=asset.workspace_id,
            filename=asset.filename,
            media_type=asset.media_type,
            size_bytes=asset.size_bytes,
            content_sha256=asset.content_sha256,
            source_kind=asset.source.kind,
            source_principal_type=principal.principal_type.value if principal else None,
            source_principal_id=principal.principal_id if principal else None,
            source_run_attempt_id=run_attempt_id,
            source_invocation_id=invocation_id,
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
            source_kind=asset.source.kind,
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            now=now,
        )
    )


async def load_upload_replay(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    identity: IdempotencyIdentity,
    now: datetime,
) -> Asset | None:
    key = entity_key(
        actor,
        operation=UPLOAD_OPERATION,
        scope_id=workspace_id,
        key_digest=identity.key_digest,
        workspace_id=workspace_id,
    )
    record = await find_by_key(session, AssetRecord, key)
    return None if record is None else record.to_resource()


async def authorize_asset_workspace(
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
        raise authorization_error(error, not_found_code=not_found_code) from error


def authorization_error(error: AuthorizationError, *, not_found_code: str = "resource_not_found") -> AssetError:
    return AssetError(
        not_found_code if error.concealed else "permission_denied",
        "The requested resource was not found." if error.concealed else "Permission denied.",
        category=ErrorCategory.not_found if error.concealed else ErrorCategory.forbidden,
    )


def idempotency_key_digest(key: str) -> str:
    try:
        return digest_visible_ascii_key(key)
    except InvalidIdempotencyKey as error:
        raise invalid_idempotency_key() from error


def invalid_idempotency_key() -> AssetError:
    return AssetError(
        "invalid_request",
        "Idempotency-Key must contain 1 through 512 visible ASCII bytes.",
        category=ErrorCategory.invalid_request,
    )


def canonical_upload_digest(
    *,
    workspace_id: str,
    filename: str,
    media_type: str,
    size_bytes: int,
    content_sha256: str,
) -> str:
    return digest_request(
        {
            "content_sha256": content_sha256,
            "filename": filename,
            "media_type": media_type,
            "size_bytes": size_bytes,
            "workspace_id": workspace_id,
        }
    )
