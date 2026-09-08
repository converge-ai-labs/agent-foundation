"""Worker preparation for the exact Skill locks frozen on an accepted Run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Protocol

from a13n_harness.capabilities import SkillManager, SkillsPolicy
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session

from .domain import (
    SkillPackageManifest,
    SkillRevisionLock,
)
from .materialization import (
    EnvironmentSkillMaterializer,
    LockedSkillRevision,
    MaterializedSkillSource,
    SkillAttemptFence,
    SkillMaterializationPlan,
    SkillMaterializationStale,
)
from .models import SkillRevisionRecord
from .objects import SkillPackageStore, SkillPackageStoreError

MAX_EFFECTIVE_SKILLS = 512
_MATERIALIZATION_ROOT = "/environment/workspace/.a13n/skills/version-1"


class ResolvedSkillLock(Protocol):
    """Structural view of one exact lock from an EffectiveAgentConfig."""

    @property
    def skill_id(self) -> str: ...

    @property
    def skill_revision_id(self) -> str: ...

    @property
    def skill_key(self) -> str: ...

    @property
    def version(self) -> int: ...

    @property
    def content_digest(self) -> str: ...


type SkillRuntimeErrorCode = Literal[
    "skill_materialization_invalid",
    "skill_materialization_unavailable",
    "skill_materialization_stale",
]


class SkillRuntimeError(RuntimeError):
    """Safe pre-Harness failure mapped into the RunAttempt lifecycle by the Worker."""

    def __init__(self, code: SkillRuntimeErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PreparedSkillRuntime:
    """Exact Harness inputs for one accepted Run's managed Skill selection."""

    manager: SkillManager | None
    catalog_digest: str | None
    materialization_root: str | None


class SkillRuntimePreparer:
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
        locks: tuple[ResolvedSkillLock, ...],
        fence: SkillAttemptFence | None = None,
    ) -> PreparedSkillRuntime:
        selected_locks = _validate_locks(locks)
        if not selected_locks:
            return PreparedSkillRuntime(
                manager=None,
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
                manifest = SkillPackageManifest.model_validate(record.manifest)
            except ValidationError as error:
                raise _invalid() from error
            if (
                record.content_digest != lock.content_digest
                or record.skill_id != lock.skill_id
                or record.version != lock.version
                or manifest.skill_name != lock.skill_key
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
        root = f"{_MATERIALIZATION_ROOT}/{catalog_digest}"
        source_id = f"service-skills-{catalog_digest[:24]}"
        plan = SkillMaterializationPlan(
            target_root=root,
            catalog_digest=catalog_digest,
            revisions=tuple(locked_revisions),
            organization_id=organization_id,
            workspace_id=workspace_id,
        )
        materializer = EnvironmentSkillMaterializer(
            f"service-materializer-{catalog_digest[:24]}",
            plan,
            self._packages,
            fence=fence,
        )
        manager = SkillManager(
            (MaterializedSkillSource(source_id, plan, fence=fence),),
            materializers=(materializer,),
            policy=SkillsPolicy(conflict="error", max_skills=max(1, len(locked_revisions))),
        )
        return PreparedSkillRuntime(
            manager=manager,
            catalog_digest=catalog_digest,
            materialization_root=root,
        )

    async def _load_records(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        locks: tuple[SkillRevisionLock, ...],
    ) -> dict[str, SkillRevisionRecord]:
        revision_ids = tuple(item.skill_revision_id for item in locks)
        if not revision_ids:
            return {}
        try:
            async with short_session(self._sessions) as session:
                records = tuple(
                    (
                        await session.scalars(
                            select(SkillRevisionRecord).where(
                                SkillRevisionRecord.organization_id == organization_id,
                                SkillRevisionRecord.workspace_id == workspace_id,
                                SkillRevisionRecord.id.in_(revision_ids),
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


def _catalog_digest(locks: tuple[SkillRevisionLock, ...]) -> str:
    payload = [item.model_dump(mode="json") for item in locks]
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(b"a13n.service.skill-runtime.v1\n" + encoded).hexdigest()


def _validate_locks(locks: tuple[ResolvedSkillLock, ...]) -> tuple[SkillRevisionLock, ...]:
    if len(locks) > MAX_EFFECTIVE_SKILLS:
        raise _invalid()
    revision_ids = tuple(item.skill_revision_id for item in locks)
    skill_ids = tuple(item.skill_id for item in locks)
    keys = tuple(item.skill_key for item in locks)
    if (
        len(revision_ids) != len(set(revision_ids))
        or len(skill_ids) != len(set(skill_ids))
        or len(keys) != len(set(keys))
    ):
        raise _invalid()
    try:
        return tuple(
            SkillRevisionLock(
                skill_id=item.skill_id,
                skill_revision_id=item.skill_revision_id,
                skill_key=item.skill_key,
                version=item.version,
                content_digest=item.content_digest,
            )
            for item in locks
        )
    except ValidationError as error:
        raise _invalid() from error


def _invalid() -> SkillRuntimeError:
    return SkillRuntimeError(
        "skill_materialization_invalid",
        "A locked Skill revision or accepted selection is invalid.",
    )
