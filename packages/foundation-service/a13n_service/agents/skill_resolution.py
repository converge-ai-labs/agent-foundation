"""Two-phase resolution for stable Skill bindings and exact Run locks."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.skills.domain import SkillPackageManifest, SkillRevisionLock
from a13n_service.skills.models import SkillRecord, SkillRevisionRecord

from .domain import ResolvedSkillBinding, SkillSelection


class SkillSelectionInvalid(RuntimeError):
    """A selected Skill cannot be resolved without changing its meaning."""


@dataclass(frozen=True, slots=True)
class PinnedSkillRevisionEvidence:
    revision_id: str
    content_digest: str


@dataclass(frozen=True, slots=True)
class PreparedSkillBinding:
    binding: ResolvedSkillBinding
    pinned: PinnedSkillRevisionEvidence | None


@dataclass(frozen=True, slots=True)
class PreparedSkillLock:
    binding: ResolvedSkillBinding
    revision_id: str
    revision_version: int
    content_digest: str


async def prepare_skill_bindings(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    selections: tuple[SkillSelection, ...],
) -> tuple[PreparedSkillBinding, ...]:
    """Resolve public keys to stable active identities for an AgentRevision."""

    if not selections:
        return ()
    keys = tuple(item.skill_key for item in selections)
    records = tuple(
        (
            await session.scalars(
                select(SkillRecord).where(
                    SkillRecord.organization_id == organization_id,
                    SkillRecord.workspace_id == workspace_id,
                    SkillRecord.key.in_(keys),
                    SkillRecord.deleted_at.is_(None),
                )
            )
        ).all()
    )
    by_key = {record.key: record for record in records}
    if set(by_key) != set(keys):
        raise SkillSelectionInvalid
    bindings = tuple(
        ResolvedSkillBinding(
            skill_id=by_key[selection.skill_key].id,
            skill_key=selection.skill_key,
            version=selection.version,
        )
        for selection in selections
    )
    if len({item.skill_id for item in bindings}) != len(bindings):
        raise SkillSelectionInvalid
    pinned = await _load_pinned_revisions(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        bindings=bindings,
    )
    return tuple(
        PreparedSkillBinding(
            binding=binding,
            pinned=(
                PinnedSkillRevisionEvidence(
                    revision_id=pinned[(binding.skill_id, binding.version)].id,
                    content_digest=pinned[(binding.skill_id, binding.version)].content_digest,
                )
                if binding.version is not None
                else None
            ),
        )
        for binding in bindings
    )


async def freeze_skill_bindings(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    prepared: tuple[PreparedSkillBinding, ...],
) -> tuple[ResolvedSkillBinding, ...]:
    """Lock and recheck stable identities before committing an AgentRevision."""

    if not prepared:
        return ()
    bindings = tuple(item.binding for item in prepared)
    records = await _load_active_skills_by_id(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        skill_ids=tuple(item.skill_id for item in bindings),
        for_update=True,
    )
    if any(records.get(binding.skill_id, None) is None for binding in bindings):
        raise SkillSelectionInvalid
    if any(records[binding.skill_id].key != binding.skill_key for binding in bindings):
        raise SkillSelectionInvalid
    pinned = await _load_pinned_revisions(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        bindings=bindings,
        for_update=True,
    )
    for expected in prepared:
        if expected.binding.version is None and expected.pinned is None:
            continue
        if expected.binding.version is None or expected.pinned is None:
            raise SkillSelectionInvalid
        revision = pinned[(expected.binding.skill_id, expected.binding.version)]
        if revision.id != expected.pinned.revision_id or revision.content_digest != expected.pinned.content_digest:
            raise SkillSelectionInvalid
    return bindings


async def prepare_skill_locks_from_bindings(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    bindings: tuple[ResolvedSkillBinding, ...],
) -> tuple[PreparedSkillLock, ...]:
    """Resolve frozen AgentRevision policies to exact active revisions."""

    if not bindings:
        return ()
    _require_unique_bindings(bindings)
    records = await _load_active_skills_by_id(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        skill_ids=tuple(item.skill_id for item in bindings),
    )
    if set(records) != {item.skill_id for item in bindings}:
        raise SkillSelectionInvalid
    if any(records[item.skill_id].key != item.skill_key for item in bindings):
        raise SkillSelectionInvalid
    return await _prepare_locks(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        bindings=bindings,
        skills=records,
    )


async def prepare_skill_locks_from_selections(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    selections: tuple[SkillSelection, ...],
) -> tuple[PreparedSkillLock, ...]:
    """Resolve one Run override from active keys directly to exact revisions."""

    prepared = await prepare_skill_bindings(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        selections=selections,
    )
    return await prepare_skill_locks_from_bindings(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        bindings=tuple(item.binding for item in prepared),
    )


async def freeze_skill_locks(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    prepared: tuple[PreparedSkillLock, ...],
) -> tuple[SkillRevisionLock, ...]:
    """Lock lifecycle heads and exact revisions at Run acceptance."""

    if not prepared:
        return ()
    skill_ids = tuple(item.binding.skill_id for item in prepared)
    skills = await _load_active_skills_by_id(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        skill_ids=skill_ids,
        for_update=True,
    )
    revision_ids = tuple(item.revision_id for item in prepared)
    revisions = tuple(
        (
            await session.scalars(
                select(SkillRevisionRecord)
                .where(
                    SkillRevisionRecord.organization_id == organization_id,
                    SkillRevisionRecord.workspace_id == workspace_id,
                    SkillRevisionRecord.id.in_(revision_ids),
                )
                .with_for_update()
            )
        ).all()
    )
    by_revision_id = {record.id: record for record in revisions}
    locks: list[SkillRevisionLock] = []
    for expected in prepared:
        skill = skills.get(expected.binding.skill_id)
        revision = by_revision_id.get(expected.revision_id)
        if (
            skill is None
            or skill.key != expected.binding.skill_key
            or revision is None
            or revision.skill_id != expected.binding.skill_id
            or revision.version != expected.revision_version
            or revision.content_digest != expected.content_digest
            or (expected.binding.version is None and skill.current_revision_id != revision.id)
            or (expected.binding.version is not None and expected.binding.version != revision.version)
        ):
            raise SkillSelectionInvalid
        _validate_revision(revision, expected.binding.skill_key)
        locks.append(
            SkillRevisionLock(
                skill_id=skill.id,
                skill_revision_id=revision.id,
                skill_key=skill.key,
                version=revision.version,
                content_digest=revision.content_digest,
            )
        )
    return tuple(locks)


async def _prepare_locks(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    bindings: tuple[ResolvedSkillBinding, ...],
    skills: dict[str, SkillRecord],
) -> tuple[PreparedSkillLock, ...]:
    pinned = await _load_pinned_revisions(
        session,
        organization_id=organization_id,
        workspace_id=workspace_id,
        bindings=bindings,
    )
    current_ids = tuple(skills[binding.skill_id].current_revision_id for binding in bindings if binding.version is None)
    current = tuple(
        (
            await session.scalars(
                select(SkillRevisionRecord).where(
                    SkillRevisionRecord.organization_id == organization_id,
                    SkillRevisionRecord.workspace_id == workspace_id,
                    SkillRevisionRecord.id.in_(current_ids),
                )
            )
        ).all()
    )
    by_current_id = {record.id: record for record in current}
    result: list[PreparedSkillLock] = []
    for binding in bindings:
        revision = (
            pinned[(binding.skill_id, binding.version)]
            if binding.version is not None
            else by_current_id.get(skills[binding.skill_id].current_revision_id)
        )
        if revision is None or revision.skill_id != binding.skill_id:
            raise SkillSelectionInvalid
        _validate_revision(revision, binding.skill_key)
        result.append(
            PreparedSkillLock(
                binding=binding,
                revision_id=revision.id,
                revision_version=revision.version,
                content_digest=revision.content_digest,
            )
        )
    return tuple(result)


async def _load_active_skills_by_id(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    skill_ids: tuple[str, ...],
    for_update: bool = False,
) -> dict[str, SkillRecord]:
    query = select(SkillRecord).where(
        SkillRecord.organization_id == organization_id,
        SkillRecord.workspace_id == workspace_id,
        SkillRecord.id.in_(skill_ids),
        SkillRecord.deleted_at.is_(None),
    )
    if for_update:
        query = query.with_for_update()
    records = tuple((await session.scalars(query)).all())
    return {record.id: record for record in records}


async def _load_pinned_revisions(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    bindings: tuple[ResolvedSkillBinding, ...],
    for_update: bool = False,
) -> dict[tuple[str, int], SkillRevisionRecord]:
    requested = tuple((item.skill_id, item.version) for item in bindings if item.version is not None)
    if not requested:
        return {}
    query = select(SkillRevisionRecord).where(
        SkillRevisionRecord.organization_id == organization_id,
        SkillRevisionRecord.workspace_id == workspace_id,
        tuple_(SkillRevisionRecord.skill_id, SkillRevisionRecord.version).in_(requested),
    )
    if for_update:
        query = query.with_for_update()
    records = tuple((await session.scalars(query)).all())
    by_identity = {(record.skill_id, record.version): record for record in records}
    if set(by_identity) != set(requested):
        raise SkillSelectionInvalid
    for binding in bindings:
        if binding.version is not None:
            _validate_revision(by_identity[(binding.skill_id, binding.version)], binding.skill_key)
    return by_identity


def _validate_revision(revision: SkillRevisionRecord, skill_key: str) -> None:
    try:
        manifest = SkillPackageManifest.model_validate(revision.manifest)
    except ValidationError as error:
        raise SkillSelectionInvalid from error
    if manifest.skill_name != skill_key or manifest.content_digest != revision.content_digest:
        raise SkillSelectionInvalid


def _require_unique_bindings(bindings: tuple[ResolvedSkillBinding, ...]) -> None:
    skill_ids = tuple(item.skill_id for item in bindings)
    skill_keys = tuple(item.skill_key for item in bindings)
    if len(skill_ids) != len(set(skill_ids)) or len(skill_keys) != len(set(skill_keys)):
        raise SkillSelectionInvalid
