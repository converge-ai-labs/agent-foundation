"""Application services for immutable Asset publication and management."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.digests import digest_request
from a13n_service.durable_operations.idempotency import (
    EvidenceScope,
    IdempotencyConflict,
    IdempotencyIdentity,
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
    load_evidence,
)
from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_workspace,
)

from .domain import (
    Asset,
)
from .errors import (
    AssetError,
    asset_idempotency_conflict,
)
from .models import AssetRecord

UPLOAD_OPERATION = "asset.upload"


async def load_upload_replay(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    identity: IdempotencyIdentity,
    now: datetime,
) -> Asset | None:
    try:
        evidence = await load_evidence(
            session,
            scope=EvidenceScope(
                workspace_id=workspace_id,
                actor_type=actor.principal.principal_type.value,
                actor_id=actor.principal.principal_id,
                operation=UPLOAD_OPERATION,
                scope_id=workspace_id,
                organization_id=actor.boundary_organization_id,
            ),
            identity=identity,
            now=now,
        )
    except IdempotencyConflict as error:
        raise asset_idempotency_conflict() from error
    if evidence is None:
        return None
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
