"""Focused relational lookups shared by Skill Management services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam.authorization import AuthenticatedActor

from .domain import SkillPackageManifest
from .errors import SkillError, skill_not_found
from .models import (
    SkillHeadRecord,
    SkillRecord,
    SkillRevisionRecord,
    SkillUploadRecord,
)


@dataclass(frozen=True, slots=True)
class SkillRecordWithHead:
    skill: SkillRecord
    head: SkillHeadRecord


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
            status_code=404,
        )
    if _as_utc(upload.expires_at) <= _as_utc(now):
        raise SkillError(
            "skill_upload_expired",
            "The staged Skill upload has expired.",
            status_code=409,
        )
    if upload.consumed_by_revision_id is not None:
        raise SkillError(
            "skill_upload_consumed",
            "The staged Skill upload has already been consumed.",
            status_code=409,
        )
    return upload


async def lock_active_skill(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    skill_id: str,
) -> SkillRecordWithHead:
    row = (
        await session.execute(
            select(SkillRecord, SkillHeadRecord)
            .join(SkillHeadRecord, SkillHeadRecord.skill_id == SkillRecord.id)
            .where(
                SkillRecord.id == skill_id,
                SkillRecord.organization_id == organization_id,
                SkillRecord.workspace_id == workspace_id,
                SkillRecord.deleted_at.is_(None),
            )
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        raise skill_not_found()
    return SkillRecordWithHead(skill=row[0], head=row[1])


async def require_skill(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    skill_id: str,
) -> SkillRecordWithHead:
    row = (
        await session.execute(
            select(SkillRecord, SkillHeadRecord)
            .join(SkillHeadRecord, SkillHeadRecord.skill_id == SkillRecord.id)
            .where(
                SkillRecord.id == skill_id,
                SkillRecord.organization_id == organization_id,
                SkillRecord.workspace_id == workspace_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise skill_not_found()
    return SkillRecordWithHead(skill=row[0], head=row[1])


async def require_revision(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    revision_id: str,
) -> SkillRevisionRecord:
    record = await session.scalar(
        select(SkillRevisionRecord).where(
            SkillRevisionRecord.id == revision_id,
            SkillRevisionRecord.organization_id == organization_id,
            SkillRevisionRecord.workspace_id == workspace_id,
        )
    )
    if record is None:
        raise skill_not_found()
    return record


def require_upload_manifest(upload: SkillUploadRecord) -> SkillPackageManifest:
    return SkillPackageManifest.model_validate(upload.manifest)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
