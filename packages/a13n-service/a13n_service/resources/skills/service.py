"""Skill heads and immutable revisions; packages are read and staged before each transaction opens.

The model sees each skill by the name its SKILL.md declares, which agent validation keeps distinct within an agent.
"""

import hashlib
import json
from collections.abc import Sequence

from anyio import to_thread
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.infra import cursors
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.ids import new_object_id
from a13n_service.infra.labels import label_filter
from a13n_service.infra.objects.interface import ObjectStore
from a13n_service.resources import revisions
from a13n_service.resources.rows import audit_row, given
from a13n_service.resources.skills import package
from a13n_service.resources.skills.github import GitHub
from a13n_service.resources.skills.schemas import (
    GitHubSource,
    Skill,
    SkillCreate,
    SkillFile,
    SkillManifest,
    SkillPage,
    SkillRevision,
    SkillRevisionCreate,
    SkillRevisionPage,
    SkillRevisionSummary,
    SkillUpdate,
    SourceKind,
    UploadSource,
)
from a13n_service.resources.skills.tables import SkillRevisionRow, SkillRow
from a13n_service.resources.uploads import service as uploads
from a13n_service.tenancy.access import workspace_scope
from a13n_service.tenancy.authorize import Principal, WorkspaceScope


async def resolve_skill(session: AsyncSession, workspace_id: str, skill_id: str, *, lock: bool = False) -> SkillRow:
    return await revisions.resolve_head(session, SkillRow, workspace_id, skill_id, lock=lock)


async def resolve_revision(session: AsyncSession, head: SkillRow, revision_id: str) -> SkillRevisionRow:
    return await revisions.find_revision(session, SkillRevisionRow, head.id, revision_id)


async def _read(
    objects: ObjectStore, github: GitHub, scope: WorkspaceScope, source: UploadSource | GitHubSource
) -> tuple[SkillManifest, bytes]:
    """Read and validate a package outside any transaction: the manifest its revision freezes, and its archive."""
    if isinstance(source, UploadSource):
        _, archive = await uploads.load(objects, scope, source.upload_id)
        recorded: UploadSource | GitHubSource = source
    else:
        commit, files = await github.fetch(source)
        archive = await to_thread.run_sync(package.pack, files)
        recorded = source.model_copy(update={"commit": commit})
    contents = await to_thread.run_sync(package.read_package, archive)
    manifest = SkillManifest(
        name=contents.name,
        description=contents.description,
        root=contents.root,
        files=tuple(SkillFile(path=path, size=len(content)) for path, content in sorted(contents.files.items())),
        size=sum(len(content) for content in contents.files.values()),
        package_digest=hashlib.sha256(archive).hexdigest(),
        package_size=len(archive),
        source=recorded,
    )
    return manifest, archive


async def _prepare(
    objects: ObjectStore, github: GitHub, scope: WorkspaceScope, actor: Principal, source: UploadSource | GitHubSource
) -> tuple[SkillManifest, str]:
    """A package's manifest and the object its revision references: an upload in place, GitHub content staged as
    the actor's upload."""
    manifest, archive = await _read(objects, github, scope, source)
    if isinstance(source, UploadSource):
        return manifest, uploads.object_key(scope.organization_id, source.upload_id)
    # Derived from what was read, so importing the same content again reuses the staged archive.
    identity = json.dumps(["github", source.repository, manifest.package_digest]).encode()
    receipt = await uploads.store(
        objects,
        scope,
        actor.id,
        request_key="github:" + hashlib.sha256(identity).hexdigest(),
        filename=f"{source.repository.split('/')[1]}-{manifest.package_digest[:12]}.zip",
        content_type="application/zip",
        content=archive,
    )
    return manifest, uploads.object_key(scope.organization_id, receipt.id)


async def _views(session: AsyncSession, heads: Sequence[SkillRow]) -> list[Skill]:
    """The skills as the API shows them, each with a summary of its default revision."""
    rows = await session.execute(
        select(SkillRevisionRow.id, SkillRevisionRow.number, SkillRevisionRow.config["source"].label("source")).where(
            SkillRevisionRow.id.in_({head.default_revision_id for head in heads if head.default_revision_id})
        )
    )
    defaults = {row.id: SkillRevisionSummary(id=row.id, number=row.number, source=row.source) for row in rows}
    return [
        Skill(
            id=head.id,
            organization_id=head.organization_id,
            workspace_id=head.workspace_id,
            name=head.name,
            description=head.description,
            labels=head.labels,
            default_revision_id=head.default_revision_id,
            default_revision=defaults[head.default_revision_id] if head.default_revision_id else None,
            archived_at=head.archived_at,
            version=head.version,
            created_by_id=head.created_by_id,
            updated_by_id=head.updated_by_id,
            created_at=head.created_at,
            updated_at=head.updated_at,
        )
        for head in heads
    ]


async def _view(session: AsyncSession, head: SkillRow) -> Skill:
    (view,) = await _views(session, [head])
    return view


async def create_skill(
    storage: Storage, objects: ObjectStore, github: GitHub, actor: Principal, workspace_id: str, body: SkillCreate
) -> Skill:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
    manifest, package_ref = await _prepare(objects, github, scope, actor, body.source)
    async with transaction(storage) as session:
        await workspace_scope(session, actor, scope.workspace_id, "write")
        head = SkillRow(
            id=new_object_id("sk"),
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            name=body.name or manifest.name,
            description=manifest.description if body.description is None else body.description,
            labels=body.labels,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        session.add(head)
        await session.flush()
        await revisions.publish(
            session, head, SkillRevisionRow, manifest, actor=actor, note=None, package_ref=package_ref
        )
        audit_row(session, actor, head, "create")
        await session.flush()
        return await _view(session, head)


async def get_skill(storage: Storage, actor: Principal, workspace_id: str, skill_id: str) -> Skill:
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        return await _view(session, await resolve_skill(session, scope.workspace_id, skill_id))


async def validate_package(
    storage: Storage,
    objects: ObjectStore,
    github: GitHub,
    actor: Principal,
    workspace_id: str,
    source: UploadSource | GitHubSource,
) -> SkillManifest:
    """The manifest a skill read from `source` would freeze, checked as creating one checks it; storing nothing."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
    manifest, _ = await _read(objects, github, scope, source)
    return manifest


async def list_skills(
    storage: Storage,
    actor: Principal,
    workspace_id: str,
    *,
    labels: list[str],
    q: str | None = None,
    source: SourceKind | None = None,
    archived: bool | None = None,
    limit: int,
    cursor: str | None,
) -> SkillPage:
    """`source` keeps the skills whose default revision was read from that kind of source."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "read")
        query = select(SkillRow).where(
            SkillRow.workspace_id == scope.workspace_id,
            label_filter(SkillRow.labels, labels),
            revisions.head_filter(SkillRow, q=q, archived=archived),
        )
        if source is not None:
            query = query.where(
                exists().where(
                    SkillRevisionRow.id == SkillRow.default_revision_id,
                    SkillRevisionRow.config.contains({"source": {"kind": source}}),
                )
            )
        rows, next_cursor = await cursors.id_page(
            session,
            query,
            SkillRow.id,
            kind="skills",
            owner=cursors.query_owner(scope.workspace_id, labels, q, source, archived),
            cursor=cursor,
            limit=limit,
        )
        return SkillPage(items=await _views(session, rows), next_cursor=next_cursor)


async def update_skill(
    storage: Storage, actor: Principal, workspace_id: str, skill_id: str, body: SkillUpdate, *, if_match: str | None
) -> Skill:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        head = await revisions.open_head(session, SkillRow, scope.workspace_id, skill_id, if_match)
        await revisions.update_head(session, actor, head, given(body, "name", "description", "labels"))
        return await _view(session, head)


async def set_archived(
    storage: Storage, actor: Principal, workspace_id: str, skill_id: str, *, archived: bool, if_match: str | None
) -> Skill:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        head = await resolve_skill(session, scope.workspace_id, skill_id, lock=True)
        await revisions.set_archived(session, actor, head, archived=archived, if_match=if_match)
        return await _view(session, head)


async def create_revision(
    storage: Storage,
    objects: ObjectStore,
    github: GitHub,
    actor: Principal,
    workspace_id: str,
    skill_id: str,
    body: SkillRevisionCreate,
    *,
    if_match: str | None,
) -> SkillRevision:
    """The new revision, or the default one when the package's manifest equals it."""
    async with short_session(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        revisions.require_open(await resolve_skill(session, scope.workspace_id, skill_id), if_match)
    manifest, package_ref = await _prepare(objects, github, scope, actor, body.source)
    async with transaction(storage) as session:
        await workspace_scope(session, actor, scope.workspace_id, "write")
        head = await revisions.open_head(session, SkillRow, scope.workspace_id, skill_id, if_match)
        revision, created = await revisions.publish(
            session,
            head,
            SkillRevisionRow,
            manifest,
            actor=actor,
            note=body.note,
            make_default=body.make_default,
            package_ref=package_ref,
        )
        if created:
            audit_row(session, actor, head, "revision.create", {"revision_id": revision.id})
            await session.flush()
        return revision_view(head, revision)


def revision_view(head: SkillRow, revision: SkillRevisionRow) -> SkillRevision:
    return SkillRevision(
        id=revision.id,
        skill_id=head.id,
        workspace_id=revision.workspace_id,
        number=revision.number,
        config=SkillManifest.model_validate(revision.config),
        digest=revision.digest,
        note=revision.note,
        created_by_id=revision.created_by_id,
        created_at=revision.created_at,
    )


async def get_revision(
    storage: Storage, actor: Principal, workspace_id: str, skill_id: str, revision_id: str
) -> SkillRevision:
    return await revisions.get_revision(
        storage, actor, SkillRow, SkillRevisionRow, revision_view, workspace_id, skill_id, revision_id
    )


async def list_revisions(
    storage: Storage, actor: Principal, workspace_id: str, skill_id: str, *, limit: int, cursor: str | None
) -> SkillRevisionPage:
    """Newest first."""
    items, next_cursor = await revisions.list_revisions(
        storage, actor, SkillRow, SkillRevisionRow, revision_view, workspace_id, skill_id, limit=limit, cursor=cursor
    )
    return SkillRevisionPage(items=items, next_cursor=next_cursor)


async def set_default_revision(
    storage: Storage, actor: Principal, workspace_id: str, skill_id: str, revision_id: str, *, if_match: str | None
) -> Skill:
    async with transaction(storage) as session:
        scope = await workspace_scope(session, actor, workspace_id, "write")
        head = await revisions.open_head(session, SkillRow, scope.workspace_id, skill_id, if_match)
        revision = await resolve_revision(session, head, revision_id)
        if revisions.set_default(head, revision, actor=actor):
            audit_row(session, actor, head, "revision.set_default", {"revision_id": revision.id})
            await session.flush()
        return await _view(session, head)
