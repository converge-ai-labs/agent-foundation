from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_harness.environment import EnvironmentAction, EnvironmentPermissionSet
from a13n_harness.environment.local.binding import _DirectLocalFilePolicy
from a13n_harness.environment.local.files import LocalFileOperator
from a13n_harness.errors import DefinitionError
from a13n_service.database.metadata import service_metadata
from a13n_service.iam import AuthenticatedActor, AuthorizationError
from a13n_service.iam.domain import PrincipalRef
from a13n_service.iam.models import OrganizationRecord, RoleBindingRecord, UserRecord, WorkspaceRecord
from a13n_service.skill_management.domain import (
    FoundationAgentSkillSelectionRequest,
    RunSkillSelectionRequest,
)
from a13n_service.skill_management.errors import SkillManagementError
from a13n_service.skill_management.materialization import (
    FoundationSkillMaterializationPlan,
    FoundationSkillMaterializer,
    FoundationSkillSource,
    LockedSkillRevision,
    SkillMaterializationStale,
)
from a13n_service.skill_management.models import WorkspaceSkillRecord, WorkspaceSkillRevisionRecord
from a13n_service.skill_management.objects import SkillPackageStore
from a13n_service.skill_management.package import (
    NormalizedSkillPackage,
    normalize_skill_files,
    skill_package_object_key,
)
from a13n_service.skill_management.runtime import FoundationSkillRuntimePreparer, SkillRuntimeError
from a13n_service.skill_management.selection import (
    SKILL_MATERIALIZATION_ACTIONS,
    AgentSkillLockResolver,
    resolve_run_skill_selection,
)
from a13n_service.storage import transaction
from a13n_service.storage.config import SQLiteConfig
from a13n_service.storage.object_store import LocalObjectStore
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

NOW = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
ORG_ID = "org_1234567890abcdef"
WORKSPACE_ID = "ws_1234567890abcdef"
BUILDER_ID = "usr_1234567890abcdef"
DIRECT_BUILDER_ID = "usr_abcdef1234567890"
AGENT_PRESET_ID = "agt_1234567890abcdef"
DEPLOY_SKILL_ID = "sk_1234567890abcdef"
DEPLOY_REVISION_ID = "skr_1234567890abcdef"
REVIEW_SKILL_ID = "sk_abcdef1234567890"
REVIEW_REVISION_ID = "skr_abcdef1234567890"


@dataclass(frozen=True, slots=True)
class RuntimeFixture:
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    packages: SkillPackageStore
    objects: LocalObjectStore
    resolver: AgentSkillLockResolver
    runtime: FoundationSkillRuntimePreparer
    actor: AuthenticatedActor
    deploy: NormalizedSkillPackage
    review: NormalizedSkillPackage


class ExpiringFence:
    def __init__(self, *, stale_on_call: int) -> None:
        self._stale_on_call = stale_on_call
        self.calls = 0

    async def require_current(self) -> None:
        self.calls += 1
        if self.calls >= self._stale_on_call:
            raise SkillMaterializationStale


@pytest.fixture
async def runtime_fixture(tmp_path: Path) -> AsyncIterator[RuntimeFixture]:
    engine = create_sql_engine(SQLiteConfig(path=tmp_path / "runtime.sqlite3"))
    async with engine.begin() as connection:
        await connection.run_sync(service_metadata().create_all)
    sessions = create_session_factory(engine)
    actor = AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=BUILDER_ID),
        auth_method="session",
        credential_id="ses_1234567890abcdef",
        boundary_workspace_id=WORKSPACE_ID,
        request_id="request-runtime",
    )
    deploy = _package("deploy", "Deploy safely.", (("scripts/deploy.sh", b"#!/bin/sh\n"),))
    review = _package("review", "Review carefully.", (("checklist.md", b"# Checklist\n"),))
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
        session.add(
            UserRecord(
                id=BUILDER_ID,
                email="builder@example.com",
                normalized_email="builder@example.com",
                name="Builder",
                status="active",
                email_verified_at=NOW,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            UserRecord(
                id=DIRECT_BUILDER_ID,
                email="direct-builder@example.com",
                normalized_email="direct-builder@example.com",
                name="Direct Builder",
                status="active",
                email_verified_at=NOW,
                version=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.flush()
        session.add_all(
            (
                RoleBindingRecord(
                    id="rb_org_builder1234567890",
                    organization_id=ORG_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=BUILDER_ID,
                    resource_type="organization",
                    resource_id=ORG_ID,
                    role_key="member",
                    created_by_user_id=BUILDER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_ws_builder123456789012",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=BUILDER_ID,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key="builder",
                    created_by_user_id=BUILDER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_org_direct12345678901",
                    organization_id=ORG_ID,
                    workspace_id=None,
                    principal_type="user",
                    principal_id=DIRECT_BUILDER_ID,
                    resource_type="organization",
                    resource_id=ORG_ID,
                    role_key="member",
                    created_by_user_id=BUILDER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_ws_direct123456789012",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=DIRECT_BUILDER_ID,
                    resource_type="workspace",
                    resource_id=WORKSPACE_ID,
                    role_key="viewer",
                    created_by_user_id=BUILDER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
                RoleBindingRecord(
                    id="rb_agent_direct123456789",
                    organization_id=ORG_ID,
                    workspace_id=WORKSPACE_ID,
                    principal_type="user",
                    principal_id=DIRECT_BUILDER_ID,
                    resource_type="agent_preset",
                    resource_id=AGENT_PRESET_ID,
                    role_key="builder",
                    created_by_user_id=BUILDER_ID,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            )
        )
        await session.flush()
        _add_skill(session, DEPLOY_SKILL_ID, DEPLOY_REVISION_ID, "Deploy", deploy)
        _add_skill(session, REVIEW_SKILL_ID, REVIEW_REVISION_ID, "Review", review)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    package_store = SkillPackageStore(objects)
    await package_store.publish(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, package=deploy)
    await package_store.publish(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, package=review)
    try:
        yield RuntimeFixture(
            engine=engine,
            sessions=sessions,
            packages=package_store,
            objects=objects,
            resolver=AgentSkillLockResolver(sessions),
            runtime=FoundationSkillRuntimePreparer(sessions, package_store),
            actor=actor,
            deploy=deploy,
            review=review,
        )
    finally:
        await engine.dispose()


def _package(name: str, description: str, extra: tuple[tuple[str, bytes], ...]) -> NormalizedSkillPackage:
    document = f"---\nname: {name}\ndescription: {description}\n---\n\n# {name.title()}\n".encode()
    return normalize_skill_files((("SKILL.md", document), *extra))


def _add_skill(
    session: AsyncSession,
    skill_id: str,
    revision_id: str,
    display_name: str,
    package: NormalizedSkillPackage,
) -> None:
    session.add(
        WorkspaceSkillRecord(
            id=skill_id,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            display_name=display_name,
            version=1,
            created_by_type="user",
            created_by_id=BUILDER_ID,
            created_at=NOW,
            updated_at=NOW,
            deleted_at=None,
        )
    )
    session.add(
        WorkspaceSkillRevisionRecord(
            id=revision_id,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            skill_id=skill_id,
            revision_number=1,
            content_digest=package.manifest.content_digest,
            manifest=package.manifest.model_dump(mode="json"),
            imported_from={"kind": "zip", "archive_sha256": "0" * 64},
            created_by_type="user",
            created_by_id=BUILDER_ID,
            created_at=NOW,
        )
    )


def _request(
    *,
    revision_ids: tuple[str, ...] = (DEPLOY_REVISION_ID, REVIEW_REVISION_ID),
    mode: str = "all",
    names: tuple[str, ...] = (),
) -> FoundationAgentSkillSelectionRequest:
    return FoundationAgentSkillSelectionRequest(
        available_revision_ids=revision_ids,
        materialization_mount="workspace" if revision_ids else None,
        default_mode=mode,
        default_names=names,
    )


def _permissions() -> EnvironmentPermissionSet:
    return EnvironmentPermissionSet(operations=SKILL_MATERIALIZATION_ACTIONS)


def _direct_actor() -> AuthenticatedActor:
    return AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=DIRECT_BUILDER_ID),
        auth_method="session",
        credential_id="ses_abcdef1234567890",
        boundary_workspace_id=WORKSPACE_ID,
        request_id="request-direct-builder",
    )


@pytest.mark.anyio
async def test_agent_publish_resolves_exact_locks_and_rechecks_them(
    runtime_fixture: RuntimeFixture,
) -> None:
    prepared = await runtime_fixture.resolver.prepare(
        actor=runtime_fixture.actor,
        workspace_id=WORKSPACE_ID,
        agent_preset_id=AGENT_PRESET_ID,
        request=_request(
            revision_ids=(REVIEW_REVISION_ID, DEPLOY_REVISION_ID),
            mode="exact",
            names=("deploy",),
        ),
        permission_ceiling=_permissions(),
    )

    assert tuple(item.skill_name for item in prepared.selection.available) == ("review", "deploy")
    assert prepared.selection.default_names == ("deploy",)
    async with transaction(runtime_fixture.sessions) as session:
        frozen = await runtime_fixture.resolver.freeze_in_transaction(
            session,
            prepared=prepared,
            permission_ceiling=_permissions(),
        )
    assert frozen == prepared.selection


@pytest.mark.anyio
async def test_direct_agent_builder_can_bind_only_for_its_target_agent(
    runtime_fixture: RuntimeFixture,
) -> None:
    prepared = await runtime_fixture.resolver.prepare(
        actor=_direct_actor(),
        workspace_id=WORKSPACE_ID,
        agent_preset_id=AGENT_PRESET_ID,
        request=_request(revision_ids=(DEPLOY_REVISION_ID,)),
        permission_ceiling=_permissions(),
    )

    assert tuple(item.skill_name for item in prepared.selection.available) == ("deploy",)
    with pytest.raises(AuthorizationError):
        await runtime_fixture.resolver.prepare(
            actor=_direct_actor(),
            workspace_id=WORKSPACE_ID,
            agent_preset_id="agt_abcdef1234567890",
            request=_request(revision_ids=(DEPLOY_REVISION_ID,)),
            permission_ceiling=_permissions(),
        )


@pytest.mark.anyio
async def test_agent_publish_rejects_deleted_or_permission_incomplete_selection(
    runtime_fixture: RuntimeFixture,
) -> None:
    incomplete = EnvironmentPermissionSet(operations=SKILL_MATERIALIZATION_ACTIONS - {EnvironmentAction.FILE_REMOVE})
    with pytest.raises(SkillManagementError) as permission_error:
        await runtime_fixture.resolver.prepare(
            actor=runtime_fixture.actor,
            workspace_id=WORKSPACE_ID,
            agent_preset_id=AGENT_PRESET_ID,
            request=_request(),
            permission_ceiling=incomplete,
        )
    assert permission_error.value.code == "skill_materialization_mount_invalid"

    async with transaction(runtime_fixture.sessions) as session:
        skill = await session.get(WorkspaceSkillRecord, DEPLOY_SKILL_ID)
        assert skill is not None
        skill.deleted_at = NOW
    with pytest.raises(SkillManagementError) as deleted_error:
        await runtime_fixture.resolver.prepare(
            actor=runtime_fixture.actor,
            workspace_id=WORKSPACE_ID,
            agent_preset_id=AGENT_PRESET_ID,
            request=_request(revision_ids=(DEPLOY_REVISION_ID,)),
            permission_ceiling=_permissions(),
        )
    assert deleted_error.value.code == "skill_revision_unavailable"


@pytest.mark.anyio
async def test_agent_publish_final_transaction_detects_tombstone(
    runtime_fixture: RuntimeFixture,
) -> None:
    prepared = await runtime_fixture.resolver.prepare(
        actor=runtime_fixture.actor,
        workspace_id=WORKSPACE_ID,
        agent_preset_id=AGENT_PRESET_ID,
        request=_request(revision_ids=(DEPLOY_REVISION_ID,)),
        permission_ceiling=_permissions(),
    )
    async with transaction(runtime_fixture.sessions) as session:
        skill = await session.get(WorkspaceSkillRecord, DEPLOY_SKILL_ID)
        assert skill is not None
        skill.deleted_at = NOW
    async with transaction(runtime_fixture.sessions) as session:
        with pytest.raises(SkillManagementError) as captured:
            await runtime_fixture.resolver.freeze_in_transaction(
                session,
                prepared=prepared,
                permission_ceiling=_permissions(),
            )
    assert captured.value.code == "skill_selection_changed"


@pytest.mark.anyio
async def test_agent_publish_rejects_duplicate_final_names(runtime_fixture: RuntimeFixture) -> None:
    async with transaction(runtime_fixture.sessions) as session:
        revision = await session.get(WorkspaceSkillRevisionRecord, REVIEW_REVISION_ID)
        assert revision is not None
        revision.manifest = runtime_fixture.deploy.manifest.model_dump(mode="json")
        revision.content_digest = runtime_fixture.deploy.manifest.content_digest
    with pytest.raises(SkillManagementError) as captured:
        await runtime_fixture.resolver.prepare(
            actor=runtime_fixture.actor,
            workspace_id=WORKSPACE_ID,
            agent_preset_id=AGENT_PRESET_ID,
            request=_request(),
            permission_ceiling=_permissions(),
        )
    assert captured.value.code == "skill_selection_ambiguous"


@pytest.mark.anyio
async def test_run_selection_uses_defaults_and_canonical_available_order(
    runtime_fixture: RuntimeFixture,
) -> None:
    prepared = await runtime_fixture.resolver.prepare(
        actor=runtime_fixture.actor,
        workspace_id=WORKSPACE_ID,
        agent_preset_id=AGENT_PRESET_ID,
        request=_request(mode="exact", names=("review",)),
        permission_ceiling=_permissions(),
    )
    selection = prepared.selection

    assert resolve_run_skill_selection(selection, RunSkillSelectionRequest()) == ("review",)
    assert resolve_run_skill_selection(
        selection,
        RunSkillSelectionRequest(selected_skill_names=("review", "deploy")),
    ) == ("deploy", "review")
    assert (
        resolve_run_skill_selection(
            selection,
            RunSkillSelectionRequest(selected_skill_names=()),
        )
        == ()
    )
    with pytest.raises(SkillManagementError) as captured:
        resolve_run_skill_selection(
            selection,
            RunSkillSelectionRequest(selected_skill_names=("missing",)),
        )
    assert captured.value.code == "skill_selection_invalid"


@pytest.mark.anyio
async def test_worker_reads_and_materializes_only_effective_run_selection(
    runtime_fixture: RuntimeFixture,
    tmp_path: Path,
) -> None:
    prepared = await runtime_fixture.resolver.prepare(
        actor=runtime_fixture.actor,
        workspace_id=WORKSPACE_ID,
        agent_preset_id=AGENT_PRESET_ID,
        request=_request(),
        permission_ceiling=_permissions(),
    )
    unselected_key = skill_package_object_key(
        ORG_ID,
        WORKSPACE_ID,
        runtime_fixture.deploy.manifest.content_digest,
    )
    await runtime_fixture.objects.put(unselected_key, b"corrupt", content_type="application/zip")
    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        selection=prepared.selection,
        selected_skill_names=("review",),
    )
    assert runtime.manager is not None
    assert runtime.selection_capability is not None
    assert runtime.selection_capability.names == frozenset({"review"})
    files = _files(tmp_path)

    catalog = await runtime.manager.scan(files=files)

    assert tuple(item.name for item in catalog) == ("review",)
    assert runtime.materialization_root is not None
    root = tmp_path / runtime.materialization_root.lstrip("/")
    assert (root / runtime_fixture.review.manifest.content_digest / "checklist.md").read_bytes() == b"# Checklist\n"
    assert not (root / runtime_fixture.deploy.manifest.content_digest).exists()
    assert (root / ".a13n-foundation-complete.json").is_file()


@pytest.mark.anyio
async def test_materializer_replaces_tampering_and_keeps_manifest_outside_packages(
    runtime_fixture: RuntimeFixture,
    tmp_path: Path,
) -> None:
    prepared = await runtime_fixture.resolver.prepare(
        actor=runtime_fixture.actor,
        workspace_id=WORKSPACE_ID,
        agent_preset_id=AGENT_PRESET_ID,
        request=_request(revision_ids=(DEPLOY_REVISION_ID,)),
        permission_ceiling=_permissions(),
    )
    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        selection=prepared.selection,
        selected_skill_names=("deploy",),
    )
    assert runtime.manager is not None
    assert runtime.materialization_root is not None
    files = _files(tmp_path)
    await runtime.manager.scan(files=files)
    root = tmp_path / runtime.materialization_root.lstrip("/")
    skill_root = root / runtime_fixture.deploy.manifest.content_digest
    (skill_root / "SKILL.md").write_text("tampered")
    (skill_root / "unexpected.txt").write_text("unexpected")

    catalog = await runtime.manager.scan(files=files)

    assert tuple(item.name for item in catalog) == ("deploy",)
    assert (skill_root / "SKILL.md").read_bytes() == next(
        item.content for item in runtime_fixture.deploy.files if item.path == "SKILL.md"
    )
    assert not (skill_root / "unexpected.txt").exists()
    assert not (skill_root / ".a13n-foundation-complete.json").exists()

    (root / ".a13n-foundation-complete.json").unlink()
    assert tuple(item.name for item in await runtime.manager.scan(files=files)) == ("deploy",)
    assert (root / ".a13n-foundation-complete.json").is_file()


@pytest.mark.anyio
async def test_materializer_rereads_package_without_retaining_run_lifetime_bytes(
    runtime_fixture: RuntimeFixture,
    tmp_path: Path,
) -> None:
    prepared = await runtime_fixture.resolver.prepare(
        actor=runtime_fixture.actor,
        workspace_id=WORKSPACE_ID,
        agent_preset_id=AGENT_PRESET_ID,
        request=_request(revision_ids=(DEPLOY_REVISION_ID,)),
        permission_ceiling=_permissions(),
    )
    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        selection=prepared.selection,
        selected_skill_names=("deploy",),
    )
    key = skill_package_object_key(ORG_ID, WORKSPACE_ID, runtime_fixture.deploy.manifest.content_digest)
    await runtime_fixture.objects.put(key, b"corrupt", content_type="application/zip")

    assert runtime.manager is not None
    with pytest.raises(DefinitionError) as invalid:
        await runtime.manager.scan(files=_files(tmp_path))
    assert invalid.value.code == "skill_materialization_invalid"


@pytest.mark.anyio
async def test_materializer_stops_writes_when_attempt_fence_expires_mid_package(
    runtime_fixture: RuntimeFixture,
    tmp_path: Path,
) -> None:
    prepared = await runtime_fixture.resolver.prepare(
        actor=runtime_fixture.actor,
        workspace_id=WORKSPACE_ID,
        agent_preset_id=AGENT_PRESET_ID,
        request=_request(revision_ids=(DEPLOY_REVISION_ID,)),
        permission_ceiling=_permissions(),
    )
    selected = LockedSkillRevision(
        lock=prepared.selection.available[0],
        manifest=runtime_fixture.deploy.manifest,
    )
    plan = FoundationSkillMaterializationPlan(
        target_root="/skills",
        catalog_digest="a" * 64,
        revisions=(selected,),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
    )
    fence = ExpiringFence(stale_on_call=6)
    materializer = FoundationSkillMaterializer(
        "foundation-materializer-test",
        plan,
        runtime_fixture.packages,
        fence=fence,
    )

    with pytest.raises(DefinitionError) as stale:
        await materializer.materialize(files=_files(tmp_path))

    assert stale.value.code == "skill_materialization_stale"
    assert fence.calls == 6
    assert not (tmp_path / "skills" / selected.lock.content_digest / "SKILL.md").exists()


@pytest.mark.anyio
async def test_exact_empty_run_materializes_no_package_and_scans_empty_catalog(
    runtime_fixture: RuntimeFixture,
    tmp_path: Path,
) -> None:
    prepared = await runtime_fixture.resolver.prepare(
        actor=runtime_fixture.actor,
        workspace_id=WORKSPACE_ID,
        agent_preset_id=AGENT_PRESET_ID,
        request=_request(),
        permission_ceiling=_permissions(),
    )
    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        selection=prepared.selection,
        selected_skill_names=(),
    )
    assert runtime.manager is not None
    assert await runtime.manager.scan(files=_files(tmp_path)) == ()
    assert runtime.materialization_root is not None
    root = tmp_path / runtime.materialization_root.lstrip("/")
    assert tuple(path.name for path in root.iterdir()) == (".a13n-foundation-complete.json",)


@pytest.mark.anyio
async def test_runtime_keeps_retained_deleted_revision_executable(runtime_fixture: RuntimeFixture) -> None:
    prepared = await runtime_fixture.resolver.prepare(
        actor=runtime_fixture.actor,
        workspace_id=WORKSPACE_ID,
        agent_preset_id=AGENT_PRESET_ID,
        request=_request(revision_ids=(DEPLOY_REVISION_ID,)),
        permission_ceiling=_permissions(),
    )
    async with transaction(runtime_fixture.sessions) as session:
        skill = await session.get(WorkspaceSkillRecord, DEPLOY_SKILL_ID)
        assert skill is not None
        skill.deleted_at = NOW

    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        selection=prepared.selection,
        selected_skill_names=("deploy",),
    )

    assert runtime.manager is not None


@pytest.mark.anyio
async def test_runtime_rejects_tampered_lock_and_stale_fence(
    runtime_fixture: RuntimeFixture,
    tmp_path: Path,
) -> None:
    prepared = await runtime_fixture.resolver.prepare(
        actor=runtime_fixture.actor,
        workspace_id=WORKSPACE_ID,
        agent_preset_id=AGENT_PRESET_ID,
        request=_request(revision_ids=(DEPLOY_REVISION_ID,)),
        permission_ceiling=_permissions(),
    )
    async with transaction(runtime_fixture.sessions) as session:
        revision = await session.scalar(
            select(WorkspaceSkillRevisionRecord).where(WorkspaceSkillRevisionRecord.id == DEPLOY_REVISION_ID)
        )
        assert revision is not None
        revision.content_digest = "f" * 64
    with pytest.raises(SkillRuntimeError) as invalid:
        await runtime_fixture.runtime.prepare(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            selection=prepared.selection,
            selected_skill_names=("deploy",),
        )
    assert invalid.value.code == "skill_materialization_invalid"

    class _StaleFence:
        async def require_current(self) -> None:
            raise SkillMaterializationStale

    with pytest.raises(SkillRuntimeError) as stale:
        await runtime_fixture.runtime.prepare(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            selection=prepared.selection,
            selected_skill_names=("deploy",),
            fence=_StaleFence(),
        )
    assert stale.value.code == "skill_materialization_stale"

    source = FoundationSkillSource(
        "foundation-skills-test",
        FoundationSkillMaterializationPlan(
            target_root="/skills",
            catalog_digest="a" * 64,
            revisions=(
                LockedSkillRevision(
                    lock=prepared.selection.available[0],
                    manifest=runtime_fixture.deploy.manifest,
                ),
            ),
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
        ),
        fence=_StaleFence(),
    )
    with pytest.raises(DefinitionError) as stale_scan:
        await source.catalog(files=_files(tmp_path))
    assert stale_scan.value.code == "skill_materialization_stale"


def _files(tmp_path: Path) -> LocalFileOperator:
    return LocalFileOperator(
        root=tmp_path,
        read_only=False,
        policy=_DirectLocalFilePolicy(max_value_bytes=64 * 1024 * 1024),
        mount_id="workspace",
        generation="generation-1",
    )
