"""The agent's pinned skills, materialized into the run's primary environment for the Harness skill catalog.

Shared mode publishes mutable conventional names. Isolated mode reuses immutable package directories.
Both prepare complete candidates before publication using existing Environment file operations.
Shared replacement accepts a visibility gap; concurrent no-replace publishers preserve the winner.

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
from a13n_service.resources.agents.schemas import IsolatedSkillSync, SharedSkillSync, SkillSelection, SkillSync
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
_SHARED = "/workspace/.agents/skills"
_AGENTS = "/workspace/.agents"
_DEFAULT_SYNC = SharedSkillSync()

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
        return f"{_SHARED}/{self.name}"

    @property
    def cached_directory(self) -> str:
        return f"{self.package_directory}/{self.name}"


async def resolve_skills(
    session: AsyncSession, run: RunRow, selections: Sequence[SkillSelection], *, sync: SkillSync = _DEFAULT_SYNC
) -> tuple[PinnedSkill, ...]:
    """The run's pinned skill revisions, read in its short session; pins are frozen, so archived skills load."""
    if not selections and not (isinstance(sync, SharedSkillSync) and sync.cleanup):
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
    skills: Sequence[PinnedSkill],
    *,
    runtime: Runtime,
    workspace_id: str,
    prove_lease: LeaseProof,
    cache: Cache,
    sync: SkillSync = _DEFAULT_SYNC,
) -> SkillsCapability:
    """The definition's skill catalog; the Harness materializes it when the run starts, before its first request."""
    pinned = _PinnedSkills(tuple(skills), runtime, workspace_id, prove_lease, cache=cache, sync=sync)
    policy = SkillsPolicy(conflict="error", max_skills=max(1, len(skills)))
    return SkillsCapability(SkillManager((pinned,), materializers=(pinned,), policy=policy))


class _PinnedSkills:
    """The catalog source and the materializer of the pinned skills, over one root the manager selects."""

    source_id = materializer_id = "a13n-service-skills"
    roots = (_AGENTS,)
    target_root = _AGENTS

    def __init__(
        self,
        skills: tuple[PinnedSkill, ...],
        runtime: Runtime,
        workspace_id: str,
        prove_lease: LeaseProof,
        *,
        cache: Cache,
        sync: SkillSync = _DEFAULT_SYNC,
    ):
        self.skills, self.runtime, self.workspace_id, self.prove_lease = skills, runtime, workspace_id, prove_lease
        self.cache, self.sync = cache, sync

    async def catalog(self, *, files: FileOperator) -> tuple[SkillCatalogItem, ...]:
        # The manifest holds the frontmatter the Harness validated when the revision was created.
        return tuple(
            SkillCatalogItem(
                name=skill.name,
                description=skill.description,
                path=skill.cached_directory if isinstance(self.sync, IsolatedSkillSync) else skill.directory,
            )
            for skill in self.skills
        )

    async def materialize(self, *, files: FileOperator) -> None:
        try:
            if isinstance(self.sync, SharedSkillSync):
                await self._shared(files, self.sync)
            else:
                await self._materialize(files)
                for skill in self.skills:
                    await self.cache.touch(files, skill.digest)
                await self.cache.trim(files, capacity=self.sync.cache_count)
        except EnvironmentError as error:
            raise ServiceError(
                "unavailable",
                "Skills could not be materialized",
                {"dependency": "environment", "code": error.code},
            ) from error

    async def _shared(self, files: FileOperator, sync: SharedSkillSync) -> None:
        selected = [
            skill for skill in self.skills if sync.overwrite or await placement.stat(files, skill.directory) is None
        ]
        if not selected and not sync.cleanup:
            return
        await self.prove_lease()
        packages = (
            await load_packages(
                self.runtime.storage, self.runtime.objects, self.workspace_id, [skill.revision_id for skill in selected]
            )
            if selected
            else ()
        )
        await files.mkdir(_SHARED, parents=True, exist_ok=True)
        for skill, package in zip(selected, packages, strict=True):
            candidate = f"{_AGENTS}/.skill-stage-{uuid4().hex}"
            for directory in sorted({posixpath.dirname(f"{candidate}/{path}") for path in package.files}):
                await files.mkdir(directory, parents=True, exist_ok=True)
            for path, content in package.files.items():
                await placement.write(files, f"{candidate}/{path}", content)
            await self.prove_lease()
            try:
                await files.move(candidate, skill.directory, replace=sync.overwrite)
            except EnvironmentError as error:
                if sync.overwrite or error.code != "environment_conflict":
                    raise
                # Additive publication preserves the concurrent winner.
                await files.remove(candidate, recursive=True)
        if sync.cleanup:
            names = {skill.name for skill in self.skills}
            stale: list[str] = []
            offset = 0
            while True:
                page = await files.list(_SHARED, offset=offset, max_results=512, include_hidden=True)
                stale.extend(entry.path for entry in page.entries if posixpath.basename(entry.path) not in names)
                if not page.has_more:
                    break
                offset += len(page.entries)
            for path in stale:
                await self.prove_lease()
                await self._discard(files, path)

    @staticmethod
    async def _discard(files: FileOperator, path: str) -> None:
        # Detach first: recursive deletion must never follow another publisher's replacement.
        discarded = f"{_AGENTS}/.skill-stage-{uuid4().hex}"
        try:
            await files.move(path, discarded)
        except EnvironmentError as error:
            if error.code == "environment_not_found":
                return
            raise
        await files.remove(discarded, recursive=True)

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
        return (await files.stat(skill.cached_directory)).kind == "directory"
    except EnvironmentError as error:
        if error.code == "environment_not_found":
            return False
        raise
