"""A revision's package bytes: the archive, one file, and every file for materializing into an environment."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from anyio import to_thread
from sqlalchemy import select

from a13n_service.infra.db import Storage, short_session
from a13n_service.infra.errors import not_found
from a13n_service.infra.objects.interface import ObjectRef, ObjectStore, read
from a13n_service.resources.skills import package
from a13n_service.resources.skills.schemas import SkillManifest
from a13n_service.resources.skills.service import resolve_revision, resolve_skill
from a13n_service.resources.skills.tables import SkillRevisionRow
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal


@dataclass(frozen=True, slots=True)
class SkillPackage:
    """One skill revision's package as plain values; `files` maps paths below the skill directory to bytes."""

    skill_id: str
    revision_id: str
    digest: str
    name: str
    description: str
    files: Mapping[str, bytes]


def _archive(revision: SkillRevisionRow) -> tuple[SkillManifest, ObjectRef]:
    manifest = SkillManifest.model_validate(revision.config)
    return manifest, ObjectRef(revision.package_ref, manifest.package_digest, manifest.package_size, "application/zip")


async def read_archive(
    storage: Storage, objects: ObjectStore, actor: Principal, workspace_id: str, skill_id: str, revision_id: str
) -> tuple[str, bytes]:
    """A download file name and the revision's package archive, verified against its digest."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        head = await resolve_skill(session, scope.workspace_id, skill_id)
        revision = await resolve_revision(session, head, revision_id)
        manifest, reference = _archive(revision)
        filename = f"{manifest.name}-{revision.number}.zip"
    return filename, await read(objects, reference)


async def read_file(
    storage: Storage,
    objects: ObjectStore,
    actor: Principal,
    workspace_id: str,
    skill_id: str,
    revision_id: str,
    path: str,
) -> bytes:
    """One file of the revision's package, by its path below the skill directory."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        head = await resolve_skill(session, scope.workspace_id, skill_id)
        manifest, reference = _archive(await resolve_revision(session, head, revision_id))
    if all(file.path != path for file in manifest.files):
        raise not_found("skill_file", path)
    files = await to_thread.run_sync(package.extract, await read(objects, reference), manifest.root, [path])
    return files[path]


async def load_packages(
    storage: Storage, objects: ObjectStore, workspace_id: str, revision_ids: Sequence[str]
) -> list[SkillPackage]:
    """The packages of pinned skill revisions, in the given order, for execution.

    Pins are frozen with the run, so archived skills still load. Rows are read in one short session; the
    object reads and unpacking happen after it closes.
    """
    async with short_session(storage) as session:
        rows = (
            await session.scalars(
                select(SkillRevisionRow).where(
                    SkillRevisionRow.workspace_id == workspace_id, SkillRevisionRow.id.in_(revision_ids)
                )
            )
        ).all()
    found = {row.id: row for row in rows}
    packages = []
    for revision_id in revision_ids:
        if (revision := found.get(revision_id)) is None:
            raise not_found("skill_revision", revision_id)
        manifest, reference = _archive(revision)
        files = await to_thread.run_sync(
            package.extract, await read(objects, reference), manifest.root, [file.path for file in manifest.files]
        )
        packages.append(
            SkillPackage(
                skill_id=revision.skill_id,
                revision_id=revision.id,
                digest=revision.digest,
                name=manifest.name,
                description=manifest.description,
                files=files,
            )
        )
    return packages
