"""Resolve exact service namespaces against retained business authority."""

import re
from datetime import datetime

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.models import AgentRecord
from a13n_service.assets.models import AssetRecord
from a13n_service.gateway.models import AguiRunBindingRecord
from a13n_service.iam.models import OrganizationRecord, UserRecord, WorkspaceRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.skills.models import SkillRevisionRecord, SkillUploadRecord

_RUN = re.compile(
    r"organizations/([^/]+)/runs/([^/]+)/(?:state\.json|display_messages\.json|payloads/[^/]+/[0-9a-f]{64}\.json)"
)
_AGUI = re.compile(r"organizations/([^/]+)/gateway/hosted-agui/([^/]+)/replay/version-1\.json")
_ASSET = re.compile(r"organizations/([^/]+)/workspaces/([^/]+)/assets/version-1/([^/]+)/content")
_SKILL = re.compile(r"organizations/([^/]+)/workspaces/([^/]+)/skills/packages/version-1/([0-9a-f]{64})\.zip")


async def retained_owner(database: AsyncSession, key: str, *, now: datetime) -> bool | None:
    """None means unknown ownership and must retain the object.

    A retained Run owns its state, replay, and payload namespace: payloads may
    still be referenced inside a retained checkpoint, beyond the public Run row.
    Neither Run history nor successful Skill publications has a new TTL.
    """
    if match := re.fullmatch(r"users/([^/]+)/profile/avatar/([^/]+)/content\.webp", key):
        user_id, image_id = match.groups()
        return bool(
            await database.scalar(select(exists().where(UserRecord.id == user_id, UserRecord.image_id == image_id)))
        )
    if match := re.fullmatch(r"organizations/([^/]+)/profile/icon/([^/]+)/content\.webp", key):
        organization_id, image_id = match.groups()
        return bool(
            await database.scalar(
                select(
                    exists().where(OrganizationRecord.id == organization_id, OrganizationRecord.image_id == image_id)
                )
            )
        )
    if match := re.fullmatch(r"organizations/([^/]+)/workspaces/([^/]+)/profile/icon/([^/]+)/content\.webp", key):
        organization_id, workspace_id, image_id = match.groups()
        return bool(
            await database.scalar(
                select(
                    exists().where(
                        WorkspaceRecord.id == workspace_id,
                        WorkspaceRecord.organization_id == organization_id,
                        WorkspaceRecord.deleted_at.is_(None),
                        WorkspaceRecord.image_id == image_id,
                    )
                )
            )
        )
    if match := re.fullmatch(
        r"organizations/([^/]+)/workspaces/([^/]+)/agents/([^/]+)/avatar/([^/]+)/content\.webp", key
    ):
        organization_id, workspace_id, agent_id, image_id = match.groups()
        return bool(
            await database.scalar(
                select(
                    exists().where(
                        AgentRecord.organization_id == organization_id,
                        AgentRecord.workspace_id == workspace_id,
                        AgentRecord.id == agent_id,
                        AgentRecord.image_id == image_id,
                    )
                )
            )
        )
    if match := _RUN.fullmatch(key):
        organization_id, run_id = match.groups()
        return bool(
            await database.scalar(
                select(
                    exists().where(
                        RunRecord.organization_id == organization_id,
                        RunRecord.id == run_id,
                    )
                )
            )
        )
    if match := _AGUI.fullmatch(key):
        organization_id, binding_id = match.groups()
        return bool(
            await database.scalar(
                select(
                    exists().where(
                        AguiRunBindingRecord.organization_id == organization_id,
                        AguiRunBindingRecord.id == binding_id,
                    )
                )
            )
        )
    if match := _ASSET.fullmatch(key):
        organization_id, workspace_id, asset_id = match.groups()
        return bool(
            await database.scalar(
                select(
                    exists().where(
                        AssetRecord.organization_id == organization_id,
                        AssetRecord.workspace_id == workspace_id,
                        AssetRecord.id == asset_id,
                    )
                )
            )
        )
    if match := _SKILL.fullmatch(key):
        organization_id, workspace_id, digest = match.groups()
        return bool(
            await database.scalar(
                select(
                    or_(
                        exists().where(
                            SkillRevisionRecord.organization_id == organization_id,
                            SkillRevisionRecord.workspace_id == workspace_id,
                            SkillRevisionRecord.content_digest == digest,
                        ),
                        exists().where(
                            SkillUploadRecord.organization_id == organization_id,
                            SkillUploadRecord.workspace_id == workspace_id,
                            SkillUploadRecord.expires_at > now,
                            SkillUploadRecord.manifest["content_digest"].as_string() == digest,
                        ),
                    )
                )
            )
        )
    return None
