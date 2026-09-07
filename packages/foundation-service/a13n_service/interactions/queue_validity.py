"""Positive terminal-deletion evidence for unaccepted queue intents."""

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.assets.models import AssetRecord
from a13n_service.iam.models import ServiceAccountRecord, WorkspaceRecord
from a13n_service.secrets.models import SecretRecord

from .control_domain import QueuedSubmission, QueuedSubmissionFailure
from .input import AssetBinarySource, BinaryContent


async def permanent_queue_failure(
    database: AsyncSession, *, organization_id: str, workspace_id: str, queued: QueuedSubmission
) -> QueuedSubmissionFailure | None:
    """Missing rows and reversible disablement are deliberately not deletion proof."""
    if await database.scalar(
        select(
            exists().where(
                WorkspaceRecord.id == workspace_id,
                WorkspaceRecord.organization_id == organization_id,
                WorkspaceRecord.deleted_at.is_not(None),
            )
        )
    ):
        return QueuedSubmissionFailure(code="workspace_deleted", message="The Workspace was permanently deleted.")
    principal = queued.authority_principal
    if principal.principal_type == "service_account" and await database.scalar(
        select(
            exists().where(
                ServiceAccountRecord.id == principal.principal_id,
                ServiceAccountRecord.organization_id == organization_id,
                ServiceAccountRecord.workspace_id == workspace_id,
                ServiceAccountRecord.deleted_at.is_not(None),
            )
        )
    ):
        return QueuedSubmissionFailure(
            code="principal_deleted", message="The queued authority was permanently deleted."
        )
    asset_ids = tuple(
        block.source.asset_id
        for block in queued.submission.input.content
        if isinstance(block, BinaryContent) and isinstance(block.source, AssetBinarySource)
    )
    if asset_ids and await database.scalar(
        select(
            exists().where(
                AssetRecord.id.in_(asset_ids),
                AssetRecord.organization_id == organization_id,
                AssetRecord.workspace_id == workspace_id,
                AssetRecord.deleted_at.is_not(None),
            )
        )
    ):
        return QueuedSubmissionFailure(code="asset_deleted", message="A required input Asset was permanently deleted.")
    hook = queued.submission.hook_subscription
    if hook is not None and await database.scalar(
        select(
            exists().where(
                SecretRecord.id == hook.webhook.signing_secret_id,
                SecretRecord.organization_id == organization_id,
                SecretRecord.workspace_id == workspace_id,
                SecretRecord.deleted_at.is_not(None),
            )
        )
    ):
        return QueuedSubmissionFailure(code="secret_deleted", message="A required Hook Secret was permanently deleted.")
    return None
