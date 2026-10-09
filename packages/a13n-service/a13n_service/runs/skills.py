"""The agent's pinned skills, materialized into the run's primary environment for the Harness skill catalog.

A pinned revision lives at `/workspace/.a13n/skills/{revision digest}`. Its package is read from the object
store, verified against the digest its revision recorded, and placed file by file; a completion marker beside
the directory is written last. An attempt, or another run sharing the environment, that finds the marker uses
the files without reading the package again. Writers of one digest write the same bytes, so concurrent
materializations converge.

Skills need the primary environment: a run that selects skills without a primary mount fails in its plan.
"""

import posixpath
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from a13n_environment.files import FileOperator
from a13n_harness.capabilities import SkillCatalogItem, SkillManager, SkillsCapability, SkillsPolicy
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import invalid, not_found
from a13n_service.resources.agents.schemas import SkillSelection
from a13n_service.resources.skills.content import load_packages
from a13n_service.resources.skills.schemas import SkillManifest
from a13n_service.resources.skills.tables import SkillRevisionRow
from a13n_service.runs import placement
from a13n_service.runs.environments.mounts import has_primary
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow

_ROOT = f"{placement.ROOT}/skills"

# Raises once the attempt no longer holds its lease; nothing is written after it fails.
type LeaseProof = Callable[[], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class PinnedSkill:
    """One pinned revision as the catalog lists it."""

    revision_id: str
    digest: str
    name: str
    description: str

    @property
    def directory(self) -> str:
        return f"{_ROOT}/{self.digest}"

    @property
    def marker(self) -> str:
        return f"{self.directory}.complete"


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
        pinned.append(PinnedSkill(row.id, row.digest, manifest.name, manifest.description))
    return tuple(pinned)


def skills_capability(
    skills: Sequence[PinnedSkill], *, runtime: Runtime, workspace_id: str, prove_lease: LeaseProof
) -> SkillsCapability:
    """The definition's skill catalog; the Harness materializes it when the run starts, before its first request."""
    pinned = _PinnedSkills(tuple(skills), runtime, workspace_id, prove_lease)
    policy = SkillsPolicy(conflict="error", max_skills=len(skills))
    return SkillsCapability(SkillManager((pinned,), materializers=(pinned,), policy=policy))


class _PinnedSkills:
    """The catalog source and the materializer of the pinned skills, over one root the manager selects."""

    source_id = materializer_id = "a13n-service-skills"
    roots = (_ROOT,)
    target_root = _ROOT

    def __init__(self, skills: tuple[PinnedSkill, ...], runtime: Runtime, workspace_id: str, prove_lease: LeaseProof):
        self.skills, self.runtime, self.workspace_id, self.prove_lease = skills, runtime, workspace_id, prove_lease

    async def catalog(self, *, files: FileOperator) -> tuple[SkillCatalogItem, ...]:
        # The manifest holds the frontmatter the Harness validated when the revision was created.
        return tuple(
            SkillCatalogItem(name=skill.name, description=skill.description, path=skill.directory)
            for skill in self.skills
        )

    async def materialize(self, *, files: FileOperator) -> None:
        missing = [skill for skill in self.skills if await placement.stat(files, skill.marker) is None]
        if not missing:
            return
        await self.prove_lease()
        packages = await load_packages(
            self.runtime.storage, self.runtime.objects, self.workspace_id, [skill.revision_id for skill in missing]
        )
        for skill, package in zip(missing, packages, strict=True):
            paths = {path: f"{skill.directory}/{path}" for path in package.files}
            for directory in sorted({posixpath.dirname(target) for target in paths.values()}):
                await files.mkdir(directory, parents=True, exist_ok=True)
            for path, target in paths.items():
                await placement.write(files, target, package.files[path])
            await files.write_text(skill.marker, skill.digest, mode="upsert")
