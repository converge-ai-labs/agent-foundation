"""Focused relational lookups shared by Skill Management services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.iam.authorization import AuthenticatedActor

from .domain import ManagedSkillPackageManifest
from .errors import SkillManagementError, skill_not_found
from .models import (
    SkillUploadRecord,
    WorkspaceSkillHeadRecord,
    WorkspaceSkillRecord,
    WorkspaceSkillRevisionRecord,
)


@dataclass(frozen=True, slots=True)
class SkillRecordWithHead:
    skill: WorkspaceSkillRecord
    head: WorkspaceSkillHeadRecord


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
        raise SkillManagementError(
            "skill_upload_not_found",
            "The staged Skill upload was not found.",
            status_code=404,
        )
    if _as_utc(upload.expires_at) <= _as_utc(now):
        raise SkillManagementError(
            "skill_upload_expired",
            "The staged Skill upload has expired.",
            status_code=409,
        )
    if upload.consumed_by_revision_id is not None:
        raise SkillManagementError(
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
            select(WorkspaceSkillRecord, WorkspaceSkillHeadRecord)
            .join(WorkspaceSkillHeadRecord, WorkspaceSkillHeadRecord.skill_id == WorkspaceSkillRecord.id)
            .where(
                WorkspaceSkillRecord.id == skill_id,
                WorkspaceSkillRecord.organization_id == organization_id,
                WorkspaceSkillRecord.workspace_id == workspace_id,
                WorkspaceSkillRecord.deleted_at.is_(None),
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
            select(WorkspaceSkillRecord, WorkspaceSkillHeadRecord)
            .join(WorkspaceSkillHeadRecord, WorkspaceSkillHeadRecord.skill_id == WorkspaceSkillRecord.id)
            .where(
                WorkspaceSkillRecord.id == skill_id,
                WorkspaceSkillRecord.organization_id == organization_id,
                WorkspaceSkillRecord.workspace_id == workspace_id,
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
) -> WorkspaceSkillRevisionRecord:
    record = await session.scalar(
        select(WorkspaceSkillRevisionRecord).where(
            WorkspaceSkillRevisionRecord.id == revision_id,
            WorkspaceSkillRevisionRecord.organization_id == organization_id,
            WorkspaceSkillRevisionRecord.workspace_id == workspace_id,
        )
    )
    if record is None:
        raise skill_not_found()
    return record


def require_upload_manifest(upload: SkillUploadRecord) -> ManagedSkillPackageManifest:
    return ManagedSkillPackageManifest.model_validate(upload.manifest)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
