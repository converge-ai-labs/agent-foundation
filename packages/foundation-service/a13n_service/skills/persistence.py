"""Focused relational lookups shared by Skill Management services."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ErrorCategory
from a13n_service.iam.authorization import AuthenticatedActor
from a13n_service.temporal import assume_utc

from .domain import SkillPackageManifest
from .errors import SkillError, skill_not_found
from .models import (
    SkillRecord,
    SkillRevisionRecord,
    SkillUploadRecord,
)


async def require_owned_upload(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    upload_id: str,
    now: datetime,
    for_update: bool = False,
) -> SkillUploadRecord:
    query = select(SkillUploadRecord).where(
        SkillUploadRecord.id == upload_id,
        SkillUploadRecord.organization_id == organization_id,
        SkillUploadRecord.workspace_id == workspace_id,
        SkillUploadRecord.uploader_type == actor.principal.principal_type.value,
        SkillUploadRecord.uploader_id == actor.principal.principal_id,
    )
    if for_update:
        query = query.with_for_update()
    upload = await session.scalar(query)
    if upload is None:
        raise SkillError(
            "skill_upload_not_found",
            "The staged Skill upload was not found.",
            category=ErrorCategory.not_found,
        )
    if assume_utc(upload.expires_at) <= assume_utc(now):
        raise SkillError(
            "skill_upload_expired",
            "The staged Skill upload has expired.",
            category=ErrorCategory.conflict,
        )
    if upload.consumed_by_revision_id is not None:
        raise SkillError(
            "skill_upload_consumed",
            "The staged Skill upload has already been consumed.",
            category=ErrorCategory.conflict,
        )
    return upload


async def lock_active_skill(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    skill_id: str,
) -> SkillRecord:
    record = await session.scalar(
        select(SkillRecord)
        .where(
            SkillRecord.id == skill_id,
            SkillRecord.organization_id == organization_id,
            SkillRecord.workspace_id == workspace_id,
            SkillRecord.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if record is None:
        raise skill_not_found()
    return record


async def require_skill(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    skill_id: str,
) -> SkillRecord:
    record = await session.scalar(
        select(SkillRecord).where(
            SkillRecord.id == skill_id,
            SkillRecord.organization_id == organization_id,
            SkillRecord.workspace_id == workspace_id,
            SkillRecord.deleted_at.is_(None),
        )
    )
    if record is None:
        raise skill_not_found()
    return record


async def require_revision(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    revision_id: str,
) -> SkillRevisionRecord:
    record = await session.scalar(
        select(SkillRevisionRecord)
        .join(SkillRecord, SkillRecord.id == SkillRevisionRecord.skill_id)
        .where(
            SkillRevisionRecord.id == revision_id,
            SkillRevisionRecord.organization_id == organization_id,
            SkillRevisionRecord.workspace_id == workspace_id,
            SkillRecord.organization_id == organization_id,
            SkillRecord.workspace_id == workspace_id,
            SkillRecord.deleted_at.is_(None),
        )
    )
    if record is None:
        raise skill_not_found()
    return record


def require_upload_manifest(upload: SkillUploadRecord) -> SkillPackageManifest:
    return SkillPackageManifest.model_validate(upload.manifest)
