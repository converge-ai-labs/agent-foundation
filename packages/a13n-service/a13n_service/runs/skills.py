"""Publish selected skills at conventional shared names, with last-publication-wins replacement.

Each complete package is prepared privately before directory publication. Concurrent runs share paths,
not version snapshots. Cleanup is an explicit opt-in and removes unselected entries from the skill root.
"""

import posixpath
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from uuid import uuid4

from a13n_harness.capabilities import SkillCatalogItem, SkillManager, SkillsCapability, SkillsPolicy
from a13n_harness.environment.files import FileOperator
from a13n_harness.environment.models import EnvironmentError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.errors import invalid, not_found
from a13n_service.resources.agents.schemas import SkillSelection
from a13n_service.resources.skills.content import load_packages
from a13n_service.resources.skills.package import validate_name
from a13n_service.resources.skills.schemas import SkillManifest
from a13n_service.resources.skills.tables import SkillRevisionRow
from a13n_service.runs import placement
from a13n_service.runs.environments.mounts import has_primary
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import RunRow

_AGENTS = "/workspace/.agents"
_ROOT = f"{_AGENTS}/skills"

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
        return f"{_ROOT}/{self.name}"


async def resolve_skills(
    session: AsyncSession, run: RunRow, selections: Sequence[SkillSelection], *, force_cleanup: bool = False
) -> tuple[PinnedSkill, ...]:
    """The run's pinned skill revisions, read in its short session; pins are frozen, so archived skills load."""
    if not selections and not force_cleanup:
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
        validate_name(manifest.name)
        pinned.append(PinnedSkill(row.id, row.digest, manifest.name, manifest.description))
    return tuple(pinned)


def skills_capability(
    skills: Sequence[PinnedSkill],
    *,
    runtime: Runtime,
    workspace_id: str,
    prove_lease: LeaseProof,
    force_overwrite: bool = True,
    force_cleanup: bool = False,
) -> SkillsCapability:
    """The definition's skill catalog; the Harness materializes it when the run starts, before its first request."""
    pinned = _PinnedSkills(
        tuple(skills), runtime, workspace_id, prove_lease, force_overwrite=force_overwrite, force_cleanup=force_cleanup
    )
    policy = SkillsPolicy(conflict="error", max_skills=max(1, len(skills)))
    return SkillsCapability(SkillManager((pinned,), materializers=(pinned,), policy=policy))


class _PinnedSkills:
    """The catalog source and the materializer of the pinned skills, over one root the manager selects."""

    source_id = materializer_id = "a13n-service-skills"
    # Staging is a sibling of skills, outside automatic skill discovery and cleanup.
    roots = (_AGENTS,)
    target_root = _AGENTS

    def __init__(
        self,
        skills: tuple[PinnedSkill, ...],
        runtime: Runtime,
        workspace_id: str,
        prove_lease: LeaseProof,
        *,
        force_overwrite: bool = True,
        force_cleanup: bool = False,
    ):
        self.skills, self.runtime, self.workspace_id, self.prove_lease = skills, runtime, workspace_id, prove_lease
        self.force_overwrite, self.force_cleanup = force_overwrite, force_cleanup

    async def catalog(self, *, files: FileOperator) -> tuple[SkillCatalogItem, ...]:
        # The manifest holds the frontmatter the Harness validated when the revision was created.
        return tuple(
            SkillCatalogItem(name=skill.name, description=skill.description, path=skill.directory)
            for skill in self.skills
        )

    async def materialize(self, *, files: FileOperator) -> None:
        selected = []
        for skill in self.skills:
            validate_name(skill.name)
            if self.force_overwrite or not await _exists(files, skill.directory):
                selected.append(skill)
        if not selected and not self.force_cleanup:
            return
        await self.prove_lease()
        packages = (
            await load_packages(
                self.runtime.storage, self.runtime.objects, self.workspace_id, [skill.revision_id for skill in selected]
            )
            if selected
            else ()
        )
        await files.mkdir(_ROOT, parents=True, exist_ok=True)
        for skill, package in zip(selected, packages, strict=True):
            candidate = f"{_AGENTS}/.skill-stage-{uuid4().hex}"
            paths = {path: f"{candidate}/{path}" for path in package.files}
            for directory in sorted({posixpath.dirname(target) for target in paths.values()}):
                await files.mkdir(directory, parents=True, exist_ok=True)
            for path, target in paths.items():
                await placement.write(files, target, package.files[path])
            await self.prove_lease()
            try:
                await files.move(candidate, skill.directory, replace=self.force_overwrite)
            except EnvironmentError as error:
                if self.force_overwrite or error.code != "environment_conflict":
                    raise
                # An additive publisher lost to another complete entry. Preserve that winner.
                await files.remove(candidate, recursive=True)
            # No finally: cancellation/unknown publication can outlive this await. Never remove
            # a candidate while the provider may still be using it.
        if self.force_cleanup:
            await self._cleanup(files)

    async def _cleanup(self, files: FileOperator) -> None:
        selected = {skill.name for skill in self.skills}
        stale: list[str] = []
        offset = 0
        while True:
            page = await files.list(_ROOT, offset=offset, max_results=512, include_hidden=True)
            stale.extend(entry.path for entry in page.entries if posixpath.basename(entry.path) not in selected)
            if not page.has_more:
                break
            offset += len(page.entries)
        for path in stale:
            await self.prove_lease()
            discarded = f"{_AGENTS}/.skill-stage-{uuid4().hex}"
            try:
                await files.move(path, discarded)
            except EnvironmentError as error:
                if error.code == "environment_not_found":
                    continue
                raise
            await files.remove(discarded, recursive=True)


async def _exists(files: FileOperator, path: str) -> bool:
    try:
        await files.stat(path)
    except EnvironmentError as error:
        if error.code == "environment_not_found":
            return False
        raise
    return True
