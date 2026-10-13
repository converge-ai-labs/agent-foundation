"""The agent's pinned skills, materialized into the run's primary environment for the Harness skill catalog.

Packages live at `/workspace/.agents/skills/{package digest}/{name}`. Each publisher writes a private
candidate before an atomic no-replace move makes the whole package visible. The published directory itself
records completion; no separate marker is needed.
Concurrent publishers reuse the winner. Each agent's catalog selects only its pinned versions; the shared
cache is never scanned for ambient skills and published package files are never overwritten.

Skills need the primary environment: a run that selects skills without a primary mount fails in its plan.
"""

import posixpath
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from uuid import uuid4

import anyio
from a13n_harness.capabilities import SkillCatalogItem, SkillManager, SkillsCapability, SkillsPolicy
from a13n_harness.environment.files import FileOperator
from a13n_harness.environment.models import EnvironmentError
from a13n_logging import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import ServiceError, invalid, not_found
from a13n_service.resources.agents.schemas import SkillSelection
from a13n_service.resources.skills.content import load_packages
from a13n_service.resources.skills.package import validate_name
from a13n_service.resources.skills.schemas import SkillManifest
from a13n_service.resources.skills.tables import SkillRevisionRow
from a13n_service.runs import placement
from a13n_service.runs.environments.mounts import has_primary
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.skill_cache import ROOT as _ROOT
from a13n_service.runs.skill_cache import Cache
from a13n_service.runs.tables import RunRow

logger = get_logger(__name__)

# Prove authority before fetching and again before publishing a candidate.
type LeaseProof = Callable[[], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class PinnedSkill:
    """One pinned revision as the catalog lists it."""

    revision_id: str
    digest: str
    name: str
    description: str

    @property
    def package_directory(self) -> str:
        return f"{_ROOT}/{self.digest}"

    @property
    def directory(self) -> str:
        return f"{self.package_directory}/{self.name}"


async def resolve_skills(
    session: AsyncSession, run: RunRow, selections: Sequence[SkillSelection]
) -> tuple[PinnedSkill, ...]:
    """The run's pinned skill revisions, read in its short session; pins are frozen, so archived skills load."""
    if not selections:
        return ()
    if not has_primary(run.environment_mounts):
        raise invalid("skills", "skills need the run's primary environment")
    revision_ids = [selection.revision_id for selection in selections]
    rows = await session.scalars(
        select(SkillRevisionRow).where(
            SkillRevisionRow.workspace_id == run.workspace_id, SkillRevisionRow.id.in_(revision_ids)
        )
    )
    found = {row.id: row for row in rows}
    pinned: list[PinnedSkill] = []
    for selection in selections:
        row = found.get(selection.revision_id or "")
        if row is None or row.skill_id != selection.skill_id:
            raise not_found("skill_revision", selection.revision_id or selection.skill_id)
        manifest = SkillManifest.model_validate(row.config)
        # Validate the name before using it as a directory component.
        validate_name(manifest.name)
        pinned.append(PinnedSkill(row.id, manifest.package_digest, manifest.name, manifest.description))
    return tuple(pinned)


def skills_capability(
    skills: Sequence[PinnedSkill], *, runtime: Runtime, workspace_id: str, prove_lease: LeaseProof, cache: Cache
) -> SkillsCapability:
    """The definition's skill catalog; the Harness materializes it when the run starts, before its first request."""
    pinned = _PinnedSkills(tuple(skills), runtime, workspace_id, prove_lease, cache=cache)
    policy = SkillsPolicy(conflict="error", max_skills=len(skills))
    return SkillsCapability(SkillManager((pinned,), materializers=(pinned,), policy=policy))


class _PinnedSkills:
    """The catalog source and the materializer of the pinned skills, over one root the manager selects."""

    source_id = materializer_id = "a13n-service-skills"
    roots = (_ROOT,)
    target_root = _ROOT

    def __init__(
        self,
        skills: tuple[PinnedSkill, ...],
        runtime: Runtime,
        workspace_id: str,
        prove_lease: LeaseProof,
        *,
        cache: Cache,
    ):
        self.skills, self.runtime, self.workspace_id, self.prove_lease = skills, runtime, workspace_id, prove_lease
        self.cache = cache

    async def catalog(self, *, files: FileOperator) -> tuple[SkillCatalogItem, ...]:
        # The manifest holds the frontmatter the Harness validated when the revision was created.
        return tuple(
            SkillCatalogItem(name=skill.name, description=skill.description, path=skill.directory)
            for skill in self.skills
        )

    async def materialize(self, *, files: FileOperator) -> None:
        try:
            await self._materialize(files)
            for skill in self.skills:
                await self.cache.touch(files, skill.digest)
            await self.cache.trim(files)
        except EnvironmentError as error:
            raise ServiceError(
                "unavailable",
                "Skill cache could not be materialized",
                {"dependency": "environment", "code": error.code},
            ) from error

    async def _materialize(self, files: FileOperator) -> None:
        uses = {skill.digest: await self.cache.acquire(files, skill.digest) for skill in self.skills}
        missing = [skill for skill in self.skills if not await _published(files, skill)]
        if not missing:
            return
        await self.prove_lease()
        packages = await load_packages(
            self.runtime.storage, self.runtime.objects, self.workspace_id, [skill.revision_id for skill in missing]
        )
        for skill, package in zip(missing, packages, strict=True):
            candidate = f"{_ROOT}/.staging/{uuid4().hex}"
            cleanup = True
            try:
                paths = {path: f"{candidate}/{skill.name}/{path}" for path in package.files}
                for directory in sorted({posixpath.dirname(target) for target in paths.values()}):
                    await files.mkdir(directory, parents=True, exist_ok=True)
                for path, target in paths.items():
                    await placement.write(files, target, package.files[path])
                await self.prove_lease()
                # Cancellation may leave the provider's rename in flight. Never race it with removal.
                cleanup = False
                uses[skill.digest].retain = True
                try:
                    await files.move(candidate, skill.package_directory, replace=False)
                    uses[skill.digest].retain = False
                except EnvironmentError as error:
                    if error.code != "environment_conflict":
                        raise
                    cleanup = True  # A rejected no-replace move cannot publish this candidate.
                    uses[skill.digest].retain = False
                    if not await _published(files, skill):
                        raise
            finally:
                if cleanup:
                    with anyio.move_on_after(5, shield=True):
                        try:
                            await files.remove(candidate, recursive=True)
                        except EnvironmentError as error:
                            if error.code != "environment_not_found":
                                logger.warning(
                                    "Skill staging cleanup failed", extra={"path": candidate, "code": error.code}
                                )


async def _published(files: FileOperator, skill: PinnedSkill) -> bool:
    try:
        return (await files.stat(skill.directory)).kind == "directory"
    except EnvironmentError as error:
        if error.code == "environment_not_found":
            return False
        raise
