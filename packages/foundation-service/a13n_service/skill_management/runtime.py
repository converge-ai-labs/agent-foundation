"""Worker preparation for immutable managed Skill locks and Turn selections."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from a13n_harness.capabilities import SkillManager, SkillSelectionRunCapability, SkillsPolicy
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session

from .domain import (
    FoundationAgentSkillSelection,
    FoundationSkillRevisionLock,
    ManagedSkillPackageManifest,
)
from .materialization import (
    FoundationSkillMaterializationPlan,
    FoundationSkillMaterializer,
    FoundationSkillSource,
    LockedSkillRevision,
    SkillAttemptFence,
    SkillMaterializationStale,
)
from .models import WorkspaceSkillRevisionRecord
from .objects import SkillPackageStore, SkillPackageStoreError
from .selection import FrozenSkillSelectionError, validate_frozen_turn_skill_selection

type SkillRuntimeErrorCode = Literal[
    "skill_materialization_invalid",
    "skill_materialization_unavailable",
    "skill_materialization_stale",
]


class SkillRuntimeError(RuntimeError):
    """Safe pre-Harness failure mapped into the TurnAttempt lifecycle by the Worker."""

    def __init__(self, code: SkillRuntimeErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PreparedSkillRuntime:
    """Exact Harness inputs for one accepted Turn's managed Skill selection."""

    manager: SkillManager | None
    selection_capability: SkillSelectionRunCapability | None
    catalog_digest: str | None
    materialization_root: str | None


class FoundationSkillRuntimePreparer:
    """Verify locked revisions and objects before constructing Harness inputs."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        packages: SkillPackageStore,
    ) -> None:
        self._sessions = sessions
        self._packages = packages

    async def prepare(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        selection: FoundationAgentSkillSelection,
        selected_skill_names: tuple[str, ...],
        fence: SkillAttemptFence | None = None,
    ) -> PreparedSkillRuntime:
        try:
            selected_locks = validate_frozen_turn_skill_selection(selection, selected_skill_names)
        except FrozenSkillSelectionError as error:
            raise _invalid() from error
        if not selection.available:
            return PreparedSkillRuntime(
                manager=None,
                selection_capability=None,
                catalog_digest=None,
                materialization_root=None,
            )
        await _require_current(fence)
        records = await self._load_records(
            organization_id=organization_id,
            workspace_id=workspace_id,
            locks=selected_locks,
        )
        locked_revisions: list[LockedSkillRevision] = []
        for lock in selected_locks:
            await _require_current(fence)
            record = records[lock.skill_revision_id]
            try:
                manifest = ManagedSkillPackageManifest.model_validate(record.manifest)
            except ValidationError as error:
                raise _invalid() from error
            if (
                record.content_digest != lock.content_digest
                or manifest.skill_name != lock.skill_name
                or manifest.content_digest != lock.content_digest
            ):
                raise _invalid()
            try:
                await self._packages.read_verified_package(
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    manifest=manifest,
                )
            except SkillPackageStoreError as error:
                code: SkillRuntimeErrorCode = (
                    "skill_materialization_unavailable"
                    if error.code == "skill_package_unavailable"
                    else "skill_materialization_invalid"
                )
                raise SkillRuntimeError(code, "A locked Skill package could not be prepared.") from error
            locked_revisions.append(LockedSkillRevision(lock=lock, manifest=manifest))
        await _require_current(fence)
        catalog_digest = _catalog_digest(selected_locks)
        mount = selection.materialization_mount
        if mount is None:
            raise _invalid()
        root = f"/environment/{mount}/.a13n/skills/version-1/{catalog_digest}"
        source_id = f"foundation-skills-{catalog_digest[:24]}"
        plan = FoundationSkillMaterializationPlan(
            target_root=root,
            catalog_digest=catalog_digest,
            revisions=tuple(locked_revisions),
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        materializer = FoundationSkillMaterializer(
            f"foundation-materializer-{catalog_digest[:24]}",
            plan,
            self._packages,
            fence=fence,
        )
        manager = SkillManager(
            (FoundationSkillSource(source_id, plan, fence=fence),),
            materializers=(materializer,),
            policy=SkillsPolicy(conflict="error", max_skills=max(1, len(locked_revisions))),
        )
        return PreparedSkillRuntime(
            manager=manager,
            selection_capability=SkillSelectionRunCapability(names=frozenset(selected_skill_names)),
            catalog_digest=catalog_digest,
            materialization_root=root,
        )

    async def _load_records(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        locks: tuple[FoundationSkillRevisionLock, ...],
    ) -> dict[str, WorkspaceSkillRevisionRecord]:
        revision_ids = tuple(item.skill_revision_id for item in locks)
        if not revision_ids:
            return {}
        try:
            async with short_session(self._sessions) as session:
                records = tuple(
                    (
                        await session.scalars(
                            select(WorkspaceSkillRevisionRecord).where(
                                WorkspaceSkillRevisionRecord.organization_id == organization_id,
                                WorkspaceSkillRevisionRecord.workspace_id == workspace_id,
                                WorkspaceSkillRevisionRecord.id.in_(revision_ids),
                            )
                        )
                    ).all()
                )
        except SQLAlchemyError as error:
            raise SkillRuntimeError(
                "skill_materialization_unavailable",
                "Skill revision storage is temporarily unavailable.",
            ) from error
        by_id = {record.id: record for record in records}
        if len(by_id) != len(revision_ids):
            raise _invalid()
        return by_id


async def _require_current(fence: SkillAttemptFence | None) -> None:
    if fence is None:
        return
    try:
        await fence.require_current()
    except SkillMaterializationStale as error:
        raise SkillRuntimeError(
            "skill_materialization_stale",
            "Skill materialization authority became stale.",
        ) from error


def _catalog_digest(locks: tuple[FoundationSkillRevisionLock, ...]) -> str:
    payload = [item.model_dump(mode="json") for item in locks]
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(b"a13n.foundation.skill-runtime.v1\n" + encoded).hexdigest()


def _invalid() -> SkillRuntimeError:
    return SkillRuntimeError(
        "skill_materialization_invalid",
        "A locked Skill revision or accepted selection is invalid.",
    )
