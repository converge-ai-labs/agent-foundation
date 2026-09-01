from __future__ import annotations

import io
import stat
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from a13n_service.database.metadata import service_metadata
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
    SkillIdempotencyRecord,
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
from a13n_service.storage.config import SQLiteConfig
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
async def skill_services(
    tmp_path: Path,
) -> AsyncIterator[SkillTestServices]:
    engine = create_sql_engine(SQLiteConfig(path=tmp_path / "skills.sqlite3"))
    async with engine.begin() as connection:
        await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    async with transaction(sessions) as session:
        session.add(OrganizationRecord(id=ORG_ID, name="Test", version=1, created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Default",
                normalized_name="default",
                version=1,
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
                    version=1,
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
    assert receipt.status_code == 201
    return ZipUploadSkillSource(upload_id=receipt.result.upload_id)


@pytest.mark.anyio
async def test_zip_stage_replays_and_conflicts_on_different_bytes(
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

    assert replay == first
    with pytest.raises(SkillError) as captured:
        await uploads.stage(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="upload-key",
            archive=archive(body="# Different"),
        )
    assert captured.value.code == "idempotency_conflict"


@pytest.mark.anyio
async def test_create_and_publish_revision_are_atomic_and_idempotent(
    skill_services: SkillTestServices,
) -> None:
    uploads = skill_services.uploads
    publication = skill_services.publication
    catalog = skill_services.catalog
    engine = skill_services.engine
    source = await staged_source(uploads, key="upload-create", content=archive())
    request = CreateSkillRequest(display_name="Deploy Helper", source=source)

    created = await publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=request,
        idempotency_key="create-key",
    )

    assert created.status_code == 201
    assert created.result.outcome == "published"
    assert created.result.skill.version == 1
    assert created.result.revision.revision_number == 1
    sessions = create_session_factory(engine)
    async with short_session(sessions) as session:
        assert await session.scalar(select(func.count()).select_from(SkillRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(SkillRevisionRecord)) == 1
        upload = await session.get(SkillUploadRecord, source.upload_id)
        assert upload is not None
        assert upload.consumed_by_revision_id == created.result.revision.id
        evidence = tuple((await session.scalars(select(SkillIdempotencyRecord))).all())
        assert len(evidence) == 2

    updated = await catalog.update(
        actor=actor(),
        skill_id=created.result.skill.id,
        request=UpdateSkillRequest(expected_version=1, display_name="Renamed"),
    )
    assert updated.version == 2
    replay = await publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=request,
        idempotency_key="create-key",
    )
    assert replay == created

    same_source = await staged_source(uploads, key="upload-same", content=archive())
    same = await publication.publish_revision(
        actor=actor(),
        skill_id=created.result.skill.id,
        request=CreateSkillRevisionRequest(expected_version=2, source=same_source),
        idempotency_key="revision-same",
    )
    assert same.status_code == 200
    assert same.result.outcome == "already_current"
    assert same.result.skill.version == 2
    assert same.result.revision.id == created.result.revision.id

    new_source = await staged_source(uploads, key="upload-new", content=archive(body="# New workflow"))
    with pytest.raises(SkillError) as stale:
        await publication.publish_revision(
            actor=actor(),
            skill_id=created.result.skill.id,
            request=CreateSkillRevisionRequest(expected_version=1, source=new_source),
            idempotency_key="revision-new",
        )
    assert stale.value.code == "skill_version_conflict"
    assert stale.value.details == {"current_version": 2}
    published = await publication.publish_revision(
        actor=actor(),
        skill_id=created.result.skill.id,
        request=CreateSkillRevisionRequest(expected_version=2, source=new_source),
        idempotency_key="revision-new",
    )
    assert published.status_code == 201
    assert published.result.skill.version == 3
    assert published.result.revision.revision_number == 2
    revision_page = await catalog.list_revisions(
        actor=actor(),
        skill_id=created.result.skill.id,
        limit=1,
        cursor=None,
    )
    assert [item.revision_number for item in revision_page.items] == [2]
    assert revision_page.next_cursor is not None
    older_page = await catalog.list_revisions(
        actor=actor(),
        skill_id=created.result.skill.id,
        limit=1,
        cursor=revision_page.next_cursor,
    )
    assert [item.revision_number for item in older_page.items] == [1]
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
        request=CreateSkillRequest(display_name="Deploy", source=source),
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
            request=UpdateSkillRequest(expected_version=1, display_name="Denied"),
        )
    assert denied.value.status_code == 404

    await catalog.delete(actor=actor(), skill_id=skill.id, expected_version=1)
    tombstone = await catalog.get(actor=actor(), skill_id=skill.id)
    assert tombstone.deleted_at == NOW
    assert tombstone.version == 2
    assert (await catalog.get_revision(actor=actor(), revision_id=revision.id)).id == revision.id
    assert (await catalog.content(actor=actor(), revision_id=revision.id))[1] == digest
    with pytest.raises(SkillError) as cannot_publish:
        await publication.publish_revision(
            actor=actor(),
            skill_id=skill.id,
            request=CreateSkillRevisionRequest(
                expected_version=2,
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
async def test_expired_idempotency_evidence_allows_a_new_upload_receipt(
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
    assert second.result.upload_id != first.result.upload_id


@pytest.mark.anyio
async def test_upload_delete_rejects_consumed_receipt_and_removes_unconsumed_receipt(
    skill_services: SkillTestServices,
) -> None:
    consumed = await staged_source(skill_services.uploads, key="consumed-upload", content=archive())
    await skill_services.publication.create(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        request=CreateSkillRequest(display_name="Consumed", source=consumed),
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


@pytest.mark.anyio
async def test_skill_collection_cursor_preserves_display_name_order(
    skill_services: SkillTestServices,
) -> None:
    for index, display_name in enumerate(("Bravo", "Alpha", "Charlie"), start=1):
        source = await staged_source(
            skill_services.uploads,
            key=f"page-upload-{index}",
            content=archive(name=f"skill-{index}"),
        )
        await skill_services.publication.create(
            actor=actor(),
            workspace_id=WORKSPACE_ID,
            request=CreateSkillRequest(display_name=display_name, source=source),
            idempotency_key=f"page-create-{index}",
        )

    first = await skill_services.catalog.list(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        limit=2,
        cursor=None,
    )
    assert [item.display_name for item in first.items] == ["Alpha", "Bravo"]
    assert first.next_cursor is not None
    second = await skill_services.catalog.list(
        actor=actor(),
        workspace_id=WORKSPACE_ID,
        limit=2,
        cursor=first.next_cursor,
    )
    assert [item.display_name for item in second.items] == ["Charlie"]
    assert second.next_cursor is None


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
        request=CreateSkillRequest(display_name="GitHub", source=source),
        idempotency_key="github-create",
    )

    assert acquirer.received_credential == "private-token-value"
    assert credentials.received_secret_id == SECRET_ID
    serialized = created.result.model_dump_json()
    assert SECRET_ID not in serialized
    assert "private-token-value" not in serialized
    assert created.result.revision.imported_from.kind == "github"


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
