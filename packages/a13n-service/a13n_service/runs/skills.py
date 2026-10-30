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
from a13n_harness.capabilities.skills import SkillSource
from a13n_harness.environment.archives import FileArchive
from a13n_harness.environment.files import FileMetadata, FileOperator, FileStatOperation
from a13n_harness.environment.models import EnvironmentError
from a13n_logging import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra.concurrency import map_concurrently
from a13n_service.infra.errors import ServiceError, invalid, not_found
from a13n_service.resources.agents.schemas import IsolatedSkillSync, SharedSkillSync, SkillSelection, SkillSync
from a13n_service.resources.skills.content import load_packages
from a13n_service.resources.skills.package import validate_name
from a13n_service.resources.skills.schemas import SkillManifest
from a13n_service.resources.skills.tables import SkillRevisionRow
from a13n_service.runs import placement
from a13n_service.runs.environments.mounts import has_primary
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.skill_cache import FILE_BATCH, Cache
from a13n_service.runs.skill_cache import ROOT as _ROOT
from a13n_service.runs.tables import RunRow

logger = get_logger(__name__)
_SHARED = "/workspace/.agents/skills"
_AGENTS = "/workspace/.agents"
_DEFAULT_SYNC = SharedSkillSync()

# Prove authority before fetching and again before publishing a candidate.
type LeaseProof = Callable[[], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class SkillPackage:
    """A pinned workspace revision or a release-owned archive, synchronized by the same policy."""

    source: str | FileArchive
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
) -> tuple[SkillPackage, ...]:
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
    pinned: list[SkillPackage] = []
    for selection in selections:
        row = found.get(selection.revision_id or "")
        if row is None or row.skill_id != selection.skill_id:
            raise not_found("skill_revision", selection.revision_id or selection.skill_id)
        manifest = SkillManifest.model_validate(row.config)
        # Validate the name before using it as a directory component.
        validate_name(manifest.name)
        pinned.append(SkillPackage(row.id, manifest.package_digest, manifest.name, manifest.description))
    return tuple(pinned)


def skills_capability(
    skills: Sequence[SkillPackage],
    *,
    runtime: Runtime,
    workspace_id: str,
    prove_lease: LeaseProof,
    cache: Cache,
    sync: SkillSync = _DEFAULT_SYNC,
    builtin: SkillSource | None = None,
) -> SkillsCapability:
    """The definition's skill catalog; the Harness materializes it when the run starts, before its first request."""
    pinned = _SkillPackages(tuple(skills), runtime, workspace_id, prove_lease, cache=cache, sync=sync)
    materialize = bool(skills) or (isinstance(sync, SharedSkillSync) and sync.cleanup)
    materializers = [pinned] if materialize else []
    sources: list[SkillSource] = [*materializers, *(() if builtin is None else (builtin,))]
    policy = SkillsPolicy(conflict="error", max_skills=max(1, len(skills) + int(builtin is not None)))
    return SkillsCapability(SkillManager(sources, materializers=materializers, policy=policy))


class _SkillPackages:
    """The catalog source and the materializer of the pinned skills, over one root the manager selects."""

    source_id = materializer_id = "a13n-service-skills"
    roots = (_AGENTS,)
    target_root = _AGENTS

    def __init__(
        self,
        skills: tuple[SkillPackage, ...],
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
                await self.cache.touch_many(files, [skill.digest for skill in self.skills])
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
        await files.mkdir(_SHARED, parents=True, exist_ok=True)
        if selected:
            await self._publish_shared(files, sync, selected)
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

    async def _publish_shared(self, files: FileOperator, sync: SharedSkillSync, selected: list[SkillPackage]) -> None:
        archives = await self._archives(selected)
        # One stage holds every candidate and its transferred archive, so a failure leaves nothing else behind.
        stage = f"{_AGENTS}/.skill-stage-{uuid4().hex}"
        cleanup = True
        try:
            await map_concurrently(
                list(zip(selected, archives, strict=True)),
                lambda item: files.write_archive(f"{stage}/{item[0].name}", item[1]),
            )
            for skill in selected:
                await self.prove_lease()
                # Cancellation may leave the provider's rename in flight. Never race it with removal.
                cleanup = False
                try:
                    await files.move(f"{stage}/{skill.name}", skill.directory, replace=sync.overwrite)
                except EnvironmentError as error:
                    if error.code != "environment_conflict":
                        raise
                    cleanup = True  # A rejected move leaves its candidate in the stage.
                    if sync.overwrite:
                        raise
                    # Additive publication preserves the concurrent winner.
                cleanup = True
        finally:
            if cleanup:
                await _remove_staging(files, stage)

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
        acquired = await self.cache.acquire_many(files, [skill.digest for skill in self.skills])
        uses = {skill.digest: use for skill, use in zip(self.skills, acquired, strict=True)}
        published = await _published(files, self.skills)
        missing = [skill for skill, exists in zip(self.skills, published, strict=True) if not exists]
        if not missing:
            return
        await self.prove_lease()
        archives = await self._archives(missing)

        async def publish(item: tuple[SkillPackage, FileArchive]) -> None:
            skill, archive = item
            candidate = f"{_ROOT}/.staging/{uuid4().hex}"
            cleanup = True
            try:
                await files.write_archive(f"{candidate}/{skill.name}", archive)
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
                    if not (await _published(files, [skill]))[0]:
                        raise
            finally:
                if cleanup:
                    await _remove_staging(files, candidate)

        await map_concurrently(list(zip(missing, archives, strict=True)), publish)

    async def _archives(self, skills: Sequence[SkillPackage]) -> list[FileArchive]:
        revisions = [skill.source for skill in skills if isinstance(skill.source, str)]
        loaded = iter(
            await load_packages(self.runtime.storage, self.runtime.objects, self.workspace_id, revisions)
            if revisions
            else ()
        )
        return [next(loaded) if isinstance(skill.source, str) else skill.source for skill in skills]


async def _published(files: FileOperator, skills: Sequence[SkillPackage]) -> list[bool]:
    published: list[bool] = []
    for offset in range(0, len(skills), FILE_BATCH):
        values = await files.batch(
            [
                FileStatOperation(path=skill.cached_directory, missing_ok=True)
                for skill in skills[offset : offset + FILE_BATCH]
            ]
        )
        published.extend(isinstance(value, FileMetadata) and value.kind == "directory" for value in values)
    return published


async def _remove_staging(files: FileOperator, path: str) -> None:
    with anyio.move_on_after(5, shield=True):
        try:
            await files.remove(path, recursive=True)
        except EnvironmentError as error:
            if error.code != "environment_not_found":
                logger.warning("Skill staging cleanup failed", extra={"path": path, "code": error.code})
