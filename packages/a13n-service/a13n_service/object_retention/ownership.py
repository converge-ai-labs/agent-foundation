"""Resolve exact service namespaces against retained business authority."""

import re
from datetime import datetime

from sqlalchemy import String, cast, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.assets.models import AssetRecord
from a13n_service.gateway.models import AguiRunBindingRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.plugins.models import PluginRuntimeLockRecord, PluginVersionRecord
from a13n_service.skills.models import SkillRevisionRecord, SkillUploadRecord

_RUN = re.compile(
    r"organizations/([^/]+)/runs/([^/]+)/(?:state\.json|replay/version-1\.json|payloads/[^/]+/[0-9a-f]{64}\.json)"
)
_AGUI = re.compile(r"organizations/([^/]+)/gateway/hosted-agui/([^/]+)/replay/version-1\.json")
_ASSET = re.compile(r"organizations/([^/]+)/workspaces/([^/]+)/assets/version-1/([^/]+)/content")
_SKILL = re.compile(r"organizations/([^/]+)/workspaces/([^/]+)/skills/packages/version-1/([0-9a-f]{64})\.zip")
_WHEEL = re.compile(r"plugins/artifacts/v1/sha256/[0-9a-f]{64}\.whl")


async def retained_owner(database: AsyncSession, key: str, *, now: datetime) -> bool | None:
    """None means unknown ownership and must retain the object.

    A retained Run owns its state, replay, and payload namespace: payloads may
    still be referenced inside a retained checkpoint, beyond the public Run row.
    Neither Run history nor successful Skill/Plugin publications has a new TTL.
    """
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
    if _WHEEL.fullmatch(key):
        return bool(
            await database.scalar(
                select(
                    or_(
                        exists().where(PluginVersionRecord.artifact_ref == key),
                        exists().where(cast(PluginRuntimeLockRecord.manifest, String).contains(key)),
                    )
                )
            )
        )
    return None
