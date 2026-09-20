from __future__ import annotations

import io
import stat
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_service.agents.domain import ResolvedSkillBinding, SkillSelection
from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.agents.skill_resolution import (
    freeze_skill_locks,
    prepare_skill_locks_from_bindings,
    prepare_skill_locks_from_selections,
)
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.etags import resource_etag
from a13n_service.http_errors import application_error_status
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.iam.domain import PrincipalRef
from a13n_service.iam.models import (
    OrganizationRecord,
    RoleBindingRecord,
    SecurityAuditRecord,
    UserRecord,
    WorkspaceRecord,
)
from a13n_service.secrets.crypto import SecretProtector
from a13n_service.secrets.models import SecretRecord
from a13n_service.skills.catalog import SkillCatalogService
from a13n_service.skills.credentials import DatabaseGitHubCredentialResolver
from a13n_service.skills.domain import (
    CreateSkillRequest,
    CreateSkillRevisionRequest,
    GitHubRevisionSource,
    GitHubSkillImportProvenance,
    UpdateSkillRequest,
    ZipUploadSkillSource,
)
from a13n_service.skills.errors import GitHubCredentialError, SkillError
from a13n_service.skills.github import AcquiredGitHubSkill
from a13n_service.skills.models import (
    SkillRecord,
    SkillRevisionRecord,
    SkillUploadRecord,
)
from a13n_service.skills.objects import SkillPackageStore
from a13n_service.skills.package import normalize_skill_files
from a13n_service.skills.publication import SkillPublicationService
from a13n_service.skills.sources import SkillSourcePreparer
from a13n_service.skills.uploads import SkillUploadService
from a13n_service.storage import short_session, transaction
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.object_store import LocalObjectStore
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
BUILDER_ID = "usr_1234567890abcdef"
VIEWER_ID = "usr_abcdef1234567890"
SECRET_ID = "sec_1234567890abcdef"


@dataclass(frozen=True, slots=True)
class SkillTestServices:
    uploads: SkillUploadService
    publication: SkillPublicationService
    catalog: SkillCatalogService
    objects: LocalObjectStore
    engine: AsyncEngine


def archive(*, name: str = "deploy-helper", body: str = "# Workflow") -> bytes:
    document = f"---\nname: {name}\ndescription: Deploy a reviewed service.\n---\n\n{body}\n".encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as target:
        info = zipfile.ZipInfo("SKILL.md")
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        target.writestr(info, document)
    return output.getvalue()


def actor(user_id: str = BUILDER_ID) -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=user_id),
        auth_method="session",
        credential_id="ses_1234567890abcdef",
        boundary_workspace_id=WORKSPACE_ID,
        request_id="request-1",
    )


@pytest.fixture
async def skill_services(tmp_path: Path, service_database: PostgreSQLConfig) -> AsyncIterator[SkillTestServices]:
    engine = create_sql_engine(service_database)
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        session.add(OrganizationRecord(id=ORG_ID, key="test", name="Test", created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Default",
                key="default",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.flush()
        for user_id in (BUILDER_ID, VIEWER_ID):
            session.add(
                UserRecord(
                    id=user_id,
                    email=f"{user_id}@example.com",
                    normalized_email=f"{user_id}@example.com",
                    name=user_id,
                    status="active",
                    email_verified_at=NOW,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        await session.flush()
        for suffix, user_id in (("builder", BUILDER_ID), ("viewer", VIEWER_ID)):
            session.add(
                RoleBindingRecord(
                    id=f"rb_org_{suffix}1234567890",
                    organization_id=ORG_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=user_id,
                    resource_type="organization",
                    resource_id=ORG_ID,
                    role_key="member",
                    created_by_user_id=BUILDER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            session.add(
                RoleBindingRecord(
                    id=f"rb_ws_{suffix}123456789012",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=user_id,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key=suffix,
                    created_by_user_id=BUILDER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
    objects = await LocalObjectStore.create(tmp_path / "objects")
    packages = SkillPackageStore(objects)
    uploads = SkillUploadService(sessions, packages, clock=lambda: NOW)
    sources = SkillSourcePreparer(sessions, packages, None, None, clock=lambda: NOW)
    publication = SkillPublicationService(sessions, sources, clock=lambda: NOW)
    catalog = SkillCatalogService(sessions, packages, clock=lambda: NOW)
    try:
        yield SkillTestServices(uploads, publication, catalog, objects, engine)
    finally:
        await engine.dispose()


async def staged_source(uploads: SkillUploadService, *, key: str, content: bytes) -> ZipUploadSkillSource:
    receipt = await uploads.stage(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key=key,
        archive=content,
    )
    assert receipt.created is True
    return ZipUploadSkillSource(upload_id=receipt.result.upload_id)


@pytest.mark.anyio
async def test_zip_stage_replays_without_comparing_archive_bytes(
    skill_services: SkillTestServices,
) -> None:
    uploads = skill_services.uploads
    first = await uploads.stage(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="upload-key",
        archive=archive(),
    )
    replay = await uploads.stage(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="upload-key",
        archive=archive(),
    )

    assert replay.result == first.result
    assert replay.created is False
    changed = await uploads.stage(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="upload-key",
        archive=archive(body="# Different"),
    )
    assert changed == replay


@pytest.mark.anyio
async def test_create_and_publish_revision_are_atomic_and_idempotent(
    skill_services: SkillTestServices,
) -> None:
    uploads = skill_services.uploads
    publication = skill_services.publication
    catalog = skill_services.catalog
    engine = skill_services.engine
    source = await staged_source(uploads, key="upload-create", content=archive())
    request = CreateSkillRequest(name="Deploy Helper", source=source)

    created = await publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=request,
        idempotency_key="create-key",
    )

    assert created.created is True
    assert created.result.outcome == "published"
    assert created.result.skill.version == 1
    assert created.result.skill.key == "deploy-helper"
    assert created.result.revision.version == 1
    sessions = create_session_factory(engine)
    async with short_session(sessions) as session:
        assert await session.scalar(select(func.count()).select_from(SkillRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(SkillRevisionRecord)) == 1
        upload = await session.get(SkillUploadRecord, source.upload_id)
        assert upload is not None
        assert upload.consumed_by_revision_id == created.result.revision.id
        evidence = tuple((await session.scalars(select(IdempotencyEvidenceRecord))).all())
        assert len(evidence) == 0

    updated = await catalog.update(
        actor=actor(),
        skill_id=created.result.skill.id,
        if_match=resource_etag(created.result.skill.id, created.result.skill.updated_at),
        request=UpdateSkillRequest(name="Renamed"),
    )
    assert updated.version == 1
    replay = await publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=request,
        idempotency_key="create-key",
    )
    assert replay.result.skill == updated
    assert replay.result.revision == created.result.revision
    assert replay.created is False

    same_source = await staged_source(uploads, key="upload-same", content=archive())
    same = await publication.publish_revision(
        actor=actor(),
        skill_id=created.result.skill.id,
        request=CreateSkillRevisionRequest(expected_version=1, source=same_source),
        idempotency_key="revision-same",
    )
    assert same.created is False
    assert same.result.outcome == "already_default"
    assert same.result.skill.version == 1
    assert same.result.revision.id == created.result.revision.id

    new_source = await staged_source(uploads, key="upload-new", content=archive(body="# New workflow"))
    published = await publication.publish_revision(
        actor=actor(),
        skill_id=created.result.skill.id,
        request=CreateSkillRevisionRequest(expected_version=1, source=new_source),
        idempotency_key="revision-new",
    )
    assert published.created is True
    assert published.result.skill.version == 2
    assert published.result.revision.version == 2
    revision_page = await catalog.list_revisions(
        actor=actor(),
        skill_id=created.result.skill.id,
        limit=1,
        cursor=None,
    )
    assert [item.version for item in revision_page.items] == [2]
    assert revision_page.next_cursor is not None
    older_page = await catalog.list_revisions(
        actor=actor(),
        skill_id=created.result.skill.id,
        limit=1,
        cursor=revision_page.next_cursor,
    )
    assert [item.version for item in older_page.items] == [1]
    assert older_page.next_cursor is None

    async with short_session(sessions) as session:
        audits = tuple(
            (await session.scalars(select(SecurityAuditRecord).where(SecurityAuditRecord.outcome == "success"))).all()
        )
        publication = next(item for item in audits if item.action == "skill.revision.publish")
        assert publication.details is not None
        assert set(publication.details) == {
            "previous_revision_id",
            "selected_revision_id",
            "source_kind",
            "publication_outcome",
        }


@pytest.mark.anyio
async def test_default_revision_selection_repoints_the_head_without_publishing(
    skill_services: SkillTestServices,
) -> None:
    uploads = skill_services.uploads
    publication = skill_services.publication
    catalog = skill_services.catalog
    created = await publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateSkillRequest(source=await staged_source(uploads, key="default-create", content=archive())),
        idempotency_key="default-create",
    )
    skill_id = created.result.skill.id
    first = created.result.revision
    second = await publication.publish_revision(
        actor=actor(),
        skill_id=skill_id,
        request=CreateSkillRevisionRequest(
            expected_version=1,
            source=await staged_source(uploads, key="default-second", content=archive(body="# Second")),
        ),
        idempotency_key="default-second",
    )
    assert second.result.skill.default_revision_id == second.result.revision.id
    other = await publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateSkillRequest(
            source=await staged_source(uploads, key="default-other", content=archive(name="other-skill"))
        ),
        idempotency_key="default-other",
    )
    published_etag = resource_etag(skill_id, second.result.skill.updated_at)

    for foreign_id in (other.result.revision.id, "skr_0000000000000000"):
        with pytest.raises(SkillError) as concealed:
            await catalog.set_default_revision(
                actor=actor(), skill_id=skill_id, revision_id=foreign_id, if_match=published_etag
            )
        assert concealed.value.code == "skill_not_found"

    restored = await catalog.set_default_revision(
        actor=actor(), skill_id=skill_id, revision_id=first.id, if_match=published_etag
    )
    assert restored.default_revision_id == first.id
    assert restored.version == 2
    assert restored.updated_at > second.result.skill.updated_at
    restored_etag = resource_etag(skill_id, restored.updated_at)
    assert restored_etag != published_etag
    assert await catalog.get(actor=actor(), skill_id=skill_id) == restored

    with pytest.raises(SkillError) as stale:
        await catalog.set_default_revision(
            actor=actor(), skill_id=skill_id, revision_id=second.result.revision.id, if_match=published_etag
        )
    assert stale.value.code == "precondition_failed"
    assert stale.value.details == {"current_etag": restored_etag}
    same = await catalog.set_default_revision(
        actor=actor(), skill_id=skill_id, revision_id=first.id, if_match=restored_etag
    )
    assert same == restored

    listed = await catalog.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=10, cursor=None, q="deploy")
    assert [(item.id, item.version, item.default_version) for item in listed.items] == [(skill_id, 2, 1)]

    # Dedup compares against the default Revision, and publication appends after the highest version.
    same_as_default = await publication.publish_revision(
        actor=actor(),
        skill_id=skill_id,
        request=CreateSkillRevisionRequest(
            expected_version=2,
            source=await staged_source(uploads, key="default-same", content=archive()),
        ),
        idempotency_key="default-same",
    )
    assert same_as_default.created is False
    assert same_as_default.result.outcome == "already_default"
    assert same_as_default.result.revision.id == first.id
    assert same_as_default.result.skill.version == 2
    same_as_highest = await publication.publish_revision(
        actor=actor(),
        skill_id=skill_id,
        request=CreateSkillRevisionRequest(
            expected_version=2,
            source=await staged_source(uploads, key="default-highest", content=archive(body="# Second")),
        ),
        idempotency_key="default-highest",
    )
    assert same_as_highest.created is True
    assert same_as_highest.result.outcome == "published"
    assert same_as_highest.result.revision.version == 3
    assert same_as_highest.result.skill.version == 3
    assert same_as_highest.result.skill.default_revision_id == same_as_highest.result.revision.id

    sessions = create_session_factory(skill_services.engine)
    async with short_session(sessions) as session:
        audits = tuple(
            (
                await session.scalars(
                    select(SecurityAuditRecord).where(SecurityAuditRecord.action == "skill.default_revision.set")
                )
            ).all()
        )
    assert sorted(item.outcome for item in audits) == ["failure", "failure", "failure", "success"]
    success = next(item for item in audits if item.outcome == "success")
    assert success.resource_id == skill_id
    assert success.details == {"previous_revision_id": second.result.revision.id, "selected_revision_id": first.id}


@pytest.mark.anyio
async def test_unpinned_binding_resolves_the_default_revision_at_run_acceptance(
    skill_services: SkillTestServices,
) -> None:
    uploads = skill_services.uploads
    publication = skill_services.publication
    created = await publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateSkillRequest(source=await staged_source(uploads, key="resolve-create", content=archive())),
        idempotency_key="resolve-create",
    )
    skill = created.result.skill
    second = await publication.publish_revision(
        actor=actor(),
        skill_id=skill.id,
        request=CreateSkillRevisionRequest(
            expected_version=1,
            source=await staged_source(uploads, key="resolve-second", content=archive(body="# Second")),
        ),
        idempotency_key="resolve-second",
    )
    restored = await skill_services.catalog.set_default_revision(
        actor=actor(),
        skill_id=skill.id,
        revision_id=created.result.revision.id,
        if_match=resource_etag(skill.id, second.result.skill.updated_at),
    )
    assert restored.version == 2

    sessions = create_session_factory(skill_services.engine)
    async with transaction(sessions) as session:
        from_binding = await prepare_skill_locks_from_bindings(
            session,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            bindings=(ResolvedSkillBinding(skill_id=skill.id, skill_key=skill.key, version=None),),
        )
        from_override = await prepare_skill_locks_from_selections(
            session,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            selections=(SkillSelection(skill_key=skill.key, version=None),),
        )
        assert from_override == from_binding
        locks = await freeze_skill_locks(
            session,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            prepared=from_binding,
        )
    assert [(lock.skill_revision_id, lock.version) for lock in locks] == [(created.result.revision.id, 1)]


@pytest.mark.anyio
async def test_active_key_conflicts_and_deleted_key_starts_a_new_identity(
    skill_services: SkillTestServices,
) -> None:
    first_source = await staged_source(skill_services.uploads, key="key-first-upload", content=archive())
    first = await skill_services.publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateSkillRequest(source=first_source),
        idempotency_key="key-first-create",
    )
    assert first.result.skill.name == "deploy-helper"
    assert first.result.skill.key == "deploy-helper"

    duplicate_source = await staged_source(
        skill_services.uploads,
        key="key-duplicate-upload",
        content=archive(body="# Different content"),
    )
    with pytest.raises(SkillError) as duplicate:
        await skill_services.publication.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            request=CreateSkillRequest(name="Another display name", source=duplicate_source),
            idempotency_key="key-duplicate-create",
        )
    assert duplicate.value.code == "skill_key_conflict"

    await skill_services.catalog.delete(
        actor=actor(),
        skill_id=first.result.skill.id,
        if_match=resource_etag(first.result.skill.id, first.result.skill.updated_at),
    )
    recreated = await skill_services.publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateSkillRequest(source=duplicate_source),
        idempotency_key="key-recreated-create",
    )
    assert recreated.result.skill.key == first.result.skill.key
    assert recreated.result.skill.id != first.result.skill.id
    assert recreated.result.revision.version == 1


@pytest.mark.anyio
async def test_revision_name_must_match_the_stable_skill_key(
    skill_services: SkillTestServices,
) -> None:
    source = await staged_source(skill_services.uploads, key="mismatch-create-upload", content=archive())
    created = await skill_services.publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateSkillRequest(source=source),
        idempotency_key="mismatch-create",
    )
    mismatch_source = await staged_source(
        skill_services.uploads,
        key="mismatch-revision-upload",
        content=archive(name="another-key"),
    )
    with pytest.raises(SkillError) as mismatch:
        await skill_services.publication.publish_revision(
            actor=actor(),
            skill_id=created.result.skill.id,
            request=CreateSkillRevisionRequest(expected_version=1, source=mismatch_source),
            idempotency_key="mismatch-revision",
        )
    assert mismatch.value.code == "skill_key_mismatch"


@pytest.mark.anyio
async def test_default_revision_references_include_disabled_unarchived_agents_and_block_delete(
    skill_services: SkillTestServices,
) -> None:
    source = await staged_source(skill_services.uploads, key="references-upload", content=archive())
    created = await skill_services.publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateSkillRequest(source=source),
        idempotency_key="references-create",
    )
    agent_id = "ap_1234567890abcdef"
    revision_id = "apr_1234567890abcdef"
    sessions = create_session_factory(skill_services.engine)
    async with transaction(sessions) as session:
        session.add(
            AgentRecord(
                id=agent_id,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                source="custom",
                name="Disabled Agent",
                key="disabled-agent",
                description=None,
                default_revision_id=revision_id,
                enabled=False,
                archived_at=None,
                duplicated_from_agent_id=None,
                duplicated_from_revision_id=None,
                created_by_type="user",
                created_by_id=BUILDER_ID,
                updated_by_type="user",
                updated_by_id=BUILDER_ID,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add(
            AgentRevisionRecord(
                id=revision_id,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                agent_id=agent_id,
                version=1,
                config={},
                config_digest="1" * 64,
                resolved_model={},
                resolved_skills=[
                    {
                        "skill_id": created.result.skill.id,
                        "skill_key": created.result.skill.key,
                        "version": None,
                    }
                ],
                connection_tools=(),
                resolved_subagents=[],
                content_digest="3" * 64,
                source_revision_id=None,
                created_by_type="user",
                created_by_id=BUILDER_ID,
                created_at=NOW,
            )
        )

    references = await skill_services.catalog.references(
        actor=actor(),
        skill_id=created.result.skill.id,
        limit=50,
        cursor=None,
    )
    assert [(item.agent_name, item.agent_id) for item in references.items] == [("Disabled Agent", agent_id)]
    with pytest.raises(SkillError) as blocked:
        await skill_services.catalog.delete(
            actor=actor(),
            skill_id=created.result.skill.id,
            if_match=resource_etag(created.result.skill.id, created.result.skill.updated_at),
        )
    assert blocked.value.code == "skill_in_use"
    assert blocked.value.details == {"blocking_agent_count": 1}
    async with short_session(sessions) as session:
        failure = await session.scalar(
            select(SecurityAuditRecord)
            .where(
                SecurityAuditRecord.action == "skill.delete",
                SecurityAuditRecord.outcome == "failure",
            )
            .order_by(SecurityAuditRecord.occurred_at.desc())
        )
        assert failure is not None
        assert failure.details == {"blocking_agent_count": 1}

    async with transaction(sessions) as session:
        agent = await session.get(AgentRecord, agent_id)
        assert agent is not None
        agent.archived_at = NOW
    await skill_services.catalog.delete(
        actor=actor(),
        skill_id=created.result.skill.id,
        if_match=resource_etag(created.result.skill.id, created.result.skill.updated_at),
    )


@pytest.mark.anyio
async def test_read_content_tombstone_and_viewer_authorization(
    skill_services: SkillTestServices,
) -> None:
    uploads = skill_services.uploads
    publication = skill_services.publication
    catalog = skill_services.catalog
    engine = skill_services.engine
    source = await staged_source(uploads, key="upload-read", content=archive())
    created = await publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateSkillRequest(name="Deploy", source=source),
        idempotency_key="create-read",
    )
    skill = created.result.skill
    revision = created.result.revision

    assert (await catalog.get(actor=actor(VIEWER_ID), skill_id=skill.id)).id == skill.id
    content, digest = await catalog.content(actor=actor(VIEWER_ID), revision_id=revision.id)
    assert content.startswith(b"PK")
    assert digest == revision.manifest.content_digest
    with pytest.raises(SkillError) as denied:
        await catalog.update(
            actor=actor(VIEWER_ID),
            skill_id=skill.id,
            if_match=resource_etag(skill.id, skill.updated_at),
            request=UpdateSkillRequest(name="Denied"),
        )
    assert application_error_status(denied.value) == 404

    await catalog.delete(
        actor=actor(),
        skill_id=skill.id,
        if_match=resource_etag(skill.id, skill.updated_at),
    )
    for read in (
        catalog.get(actor=actor(), skill_id=skill.id),
        catalog.get_by_key(actor=actor(), workspace_id=WORKSPACE_ID, skill_key=skill.key),
        catalog.get_revision(actor=actor(), revision_id=revision.id),
        catalog.content(actor=actor(), revision_id=revision.id),
    ):
        with pytest.raises(SkillError) as missing:
            await read
        assert missing.value.code == "skill_not_found"
    listed = await catalog.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=50, cursor=None)
    assert listed.items == ()
    with pytest.raises(SkillError) as cannot_publish:
        await publication.publish_revision(
            actor=actor(),
            skill_id=skill.id,
            request=CreateSkillRevisionRequest(
                expected_version=1,
                source=await staged_source(uploads, key="after-delete", content=archive(body="# Later")),
            ),
            idempotency_key="revision-after-delete",
        )
    assert cannot_publish.value.code == "skill_not_found"

    sessions = create_session_factory(engine)
    async with short_session(sessions) as session:
        failures = tuple(
            (await session.scalars(select(SecurityAuditRecord).where(SecurityAuditRecord.outcome == "failure"))).all()
        )
        assert any(item.action == "skill.update" and item.resource_id == skill.id for item in failures)
        assert any(item.action == "skill.revision.publish" and item.resource_id == skill.id for item in failures)


@pytest.mark.anyio
async def test_upload_key_survives_its_business_expiry_until_collection(
    skill_services: SkillTestServices,
) -> None:
    first = await skill_services.uploads.stage(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="expiring-key",
        archive=archive(),
    )
    late = NOW + timedelta(hours=25)
    sessions = create_session_factory(skill_services.engine)
    uploads = SkillUploadService(
        sessions,
        SkillPackageStore(skill_services.objects),
        clock=lambda: late,
    )

    with pytest.raises(SkillError) as expired:
        await uploads.get(actor=actor(), upload_id=first.result.upload_id)
    assert expired.value.code == "skill_upload_expired"
    second = await uploads.stage(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        idempotency_key="expiring-key",
        archive=archive(body="# Replacement"),
    )
    assert second.result.upload_id == first.result.upload_id


@pytest.mark.anyio
async def test_upload_delete_rejects_consumed_receipt_and_removes_unconsumed_receipt(
    skill_services: SkillTestServices,
) -> None:
    consumed = await staged_source(skill_services.uploads, key="consumed-upload", content=archive())
    await skill_services.publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateSkillRequest(name="Consumed", source=consumed),
        idempotency_key="consumed-create",
    )
    with pytest.raises(SkillError) as conflict:
        await skill_services.uploads.delete(actor=actor(), upload_id=consumed.upload_id)
    assert conflict.value.code == "skill_upload_consumed"

    unconsumed = await staged_source(skill_services.uploads, key="deletable-upload", content=archive())
    await skill_services.uploads.delete(actor=actor(), upload_id=unconsumed.upload_id)
    with pytest.raises(SkillError) as missing:
        await skill_services.uploads.get(actor=actor(), upload_id=unconsumed.upload_id)
    assert missing.value.code == "skill_upload_not_found"

    replacement = await staged_source(skill_services.uploads, key="deletable-upload", content=archive())
    assert replacement.upload_id != unconsumed.upload_id


@pytest.mark.anyio
async def test_skill_collection_cursor_preserves_name_order(
    skill_services: SkillTestServices,
) -> None:
    for index, name in enumerate(("Bravo", "Alpha", "Charlie"), start=1):
        source = await staged_source(
            skill_services.uploads,
            key=f"page-upload-{index}",
            content=archive(name=f"skill-{index}"),
        )
        await skill_services.publication.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            request=CreateSkillRequest(name=name, source=source),
            idempotency_key=f"page-create-{index}",
        )

    first = await skill_services.catalog.list(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        limit=2,
        cursor=None,
    )
    assert [item.name for item in first.items] == ["Alpha", "Bravo"]
    assert first.next_cursor is not None
    assert {item.source_kind for item in first.items} == {"zip"}
    with pytest.raises(SkillError) as mismatch:
        await skill_services.catalog.list(
            actor=actor(), workspace_id=WORKSPACE_ID, limit=2, cursor=first.next_cursor, source_kind="zip"
        )
    assert mismatch.value.code == "invalid_cursor"
    second = await skill_services.catalog.list(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        limit=2,
        cursor=first.next_cursor,
    )
    assert [item.name for item in second.items] == ["Charlie"]
    assert second.next_cursor is None

    catalog = skill_services.catalog
    keyed = await catalog.get_by_key(actor=actor(VIEWER_ID), workspace_id=WORKSPACE_ID, skill_key="skill-2")
    assert keyed.name == "Alpha"
    for key in (keyed.id, "missing"):
        with pytest.raises(SkillError, match="not found"):
            await catalog.get_by_key(actor=actor(), workspace_id=WORKSPACE_ID, skill_key=key)
    with pytest.raises(SkillError):
        await catalog.get_by_key(actor=actor("usr_0000000000000000"), workspace_id=WORKSPACE_ID, skill_key=keyed.key)
    with pytest.raises(SkillError):
        await catalog.get_by_key(actor=actor(), workspace_id="ws_0000000000000000", skill_key=keyed.key)
    by_key = await catalog.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=1, cursor=None, q="SKILL-2")
    assert [item.name for item in by_key.items] == ["Alpha"]
    page = await catalog.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=2, cursor=None, q=" A ")
    assert [item.name for item in page.items] == ["Alpha", "Bravo"]
    remainder = await catalog.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=2, cursor=page.next_cursor, q="a")
    assert [item.name for item in remainder.items] == ["Charlie"]
    with pytest.raises(SkillError) as mismatch:
        await catalog.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=2, cursor=page.next_cursor, q="b")
    assert mismatch.value.code == "invalid_cursor"
    for term in ("%", "_", "unmatched"):
        empty = await catalog.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=2, cursor=None, q=term)
        assert empty.items == ()


@pytest.mark.anyio
async def test_github_publication_uses_secret_without_persisting_selector_or_value(
    skill_services: SkillTestServices,
) -> None:
    objects = skill_services.objects
    engine = skill_services.engine
    package = normalize_skill_files(
        (("SKILL.md", b"---\nname: github-skill\ndescription: Imported safely.\n---\n\n# Run\n"),)
    )

    class Acquirer:
        received_credential: str | None = None

        async def acquire(
            self,
            source: GitHubRevisionSource,
            *,
            credential: str | None = None,
        ) -> AcquiredGitHubSkill:
            self.received_credential = credential
            return AcquiredGitHubSkill(
                package=package,
                provenance=GitHubSkillImportProvenance(
                    repository_url="https://github.com/example/skills",
                    requested_ref=source.ref,
                    resolved_commit_sha="a" * 40,
                    subdirectory=source.subdirectory,
                ),
            )

    class Credentials:
        received_secret_id: str | None = None

        async def resolve(
            self,
            *,
            actor: AuthenticatedActor,
            organization_id: str,
            workspace_id: str,
            action: WorkspaceAction,
            secret_id: str,
        ) -> str:
            del actor, organization_id, workspace_id, action
            self.received_secret_id = secret_id
            return "private-token-value"

    acquirer = Acquirer()
    credentials = Credentials()
    sessions = create_session_factory(engine)
    sources = SkillSourcePreparer(
        sessions,
        SkillPackageStore(objects),
        acquirer,
        credentials,
        clock=lambda: NOW,
    )
    publication = SkillPublicationService(sessions, sources, clock=lambda: NOW)
    source = GitHubRevisionSource(
        repository_url="https://github.com/example/skills",
        ref="main",
        credential_secret_id=SECRET_ID,
    )

    created = await publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateSkillRequest(name="GitHub", source=source),
        idempotency_key="github-create",
    )

    assert acquirer.received_credential == "private-token-value"
    assert credentials.received_secret_id == SECRET_ID
    serialized = created.result.model_dump_json()
    assert SECRET_ID not in serialized
    assert "private-token-value" not in serialized
    assert created.result.revision.imported_from.kind == "github"
    catalog = skill_services.catalog
    listed = await catalog.list(
        actor=actor(), workspace_id=WORKSPACE_ID, limit=50, cursor=None, source_kind="github", q="github"
    )
    assert [(item.id, item.source_kind) for item in listed.items] == [(created.result.skill.id, "github")]
    empty = await catalog.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=50, cursor=None, source_kind="zip")
    assert not empty.items
    upload = await staged_source(
        skill_services.uploads, key="switch-source", content=archive(name="github-skill", body="# New version")
    )
    await skill_services.publication.publish_revision(
        actor=actor(),
        skill_id=created.result.skill.id,
        request=CreateSkillRevisionRequest(source=upload, expected_version=1),
        idempotency_key="switch-source",
    )
    github = await catalog.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=50, cursor=None, source_kind="github")
    assert not github.items
    zipped = await catalog.list(actor=actor(), workspace_id=WORKSPACE_ID, limit=50, cursor=None, source_kind="zip")
    assert [(item.id, item.source_kind) for item in zipped.items] == [(created.result.skill.id, "zip")]


@pytest.mark.anyio
async def test_github_credential_resolution_reauthorizes_workspace_role(
    skill_services: SkillTestServices,
) -> None:
    engine = skill_services.engine
    protector = SecretProtector(key=b"0" * 32, encryption_key_id="test-key")
    encrypted = protector.encrypt(
        "github-token",
        secret_id=SECRET_ID,
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        owner_type="workspace",
        owner_id=WORKSPACE_ID,
        key="github_token",
        version=1,
    )
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        session.add(
            SecretRecord(
                id=SECRET_ID,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                owner_type="workspace",
                owner_id=WORKSPACE_ID,
                key="github_token",
                version=1,
                ciphertext=encrypted.ciphertext,
                nonce=encrypted.nonce,
                encryption_key_id=encrypted.encryption_key_id,
                created_at=NOW,
                value_updated_at=NOW,
                deleted_at=None,
            )
        )
    resolver = DatabaseGitHubCredentialResolver(sessions, protector)

    assert (
        await resolver.resolve(
            actor=actor(),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            action=WorkspaceAction.skill_update,
            secret_id=SECRET_ID,
        )
        == "github-token"
    )
    with pytest.raises(GitHubCredentialError):
        await resolver.resolve(
            actor=actor(VIEWER_ID),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            action=WorkspaceAction.skill_update,
            secret_id=SECRET_ID,
        )
