from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a13n_environment.direct_local.files import LocalFileOperator
from a13n_environment.direct_local.provider import _DirectLocalFilePolicy
from a13n_harness.errors import DefinitionError
from a13n_service.iam.models import OrganizationRecord, WorkspaceRecord
from a13n_service.skills.domain import SkillRevisionLock
from a13n_service.skills.materialization import (
    EnvironmentSkillMaterializer,
    LockedSkillRevision,
    MaterializedSkillSource,
    SkillMaterializationPlan,
    SkillMaterializationStale,
)
from a13n_service.skills.models import SkillRecord, SkillRevisionRecord
from a13n_service.skills.objects import SkillPackageStore
from a13n_service.skills.package import NormalizedSkillPackage, normalize_skill_files, skill_package_object_key
from a13n_service.skills.runtime import SkillRuntimeError, SkillRuntimePreparer
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
    runtime: SkillRuntimePreparer
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
async def runtime_fixture(
    tmp_path: Path,
    service_sqlite_database: Path,
) -> AsyncIterator[RuntimeFixture]:
    engine = create_sql_engine(SQLiteConfig(path=service_sqlite_database))
    sessions = create_session_factory(engine)
    deploy = _package("deploy", "Deploy safely.", (("scripts/deploy.sh", b"#!/bin/sh\n"),))
    review = _package("review", "Review carefully.", (("checklist.md", b"# Checklist\n"),))
    async with transaction(sessions) as session:
        session.add(OrganizationRecord(id=ORG_ID, name="Test", created_at=NOW, updated_at=NOW))
        await session.flush()
        session.add(
            WorkspaceRecord(
                id=WORKSPACE_ID,
                organization_id=ORG_ID,
                name="Default",
                normalized_name="default",
                created_at=NOW,
                updated_at=NOW,
                deleted_at=None,
            )
        )
        await session.flush()
        _add_skill(session, DEPLOY_SKILL_ID, DEPLOY_REVISION_ID, "Deploy", deploy)
        _add_skill(session, REVIEW_SKILL_ID, REVIEW_REVISION_ID, "Review", review)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    packages = SkillPackageStore(objects)
    await packages.publish(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, package=deploy)
    await packages.publish(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, package=review)
    try:
        yield RuntimeFixture(
            engine=engine,
            sessions=sessions,
            packages=packages,
            objects=objects,
            runtime=SkillRuntimePreparer(sessions, packages),
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
    name: str,
    package: NormalizedSkillPackage,
) -> None:
    session.add(
        SkillRecord(
            id=skill_id,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            key=package.manifest.skill_name,
            name=name,
            version=1,
            current_revision_id=revision_id,
            created_by_type="user",
            created_by_id=BUILDER_ID,
            updated_by_type="user",
            updated_by_id=BUILDER_ID,
            created_at=NOW,
            updated_at=NOW,
            deleted_at=None,
        )
    )
    session.add(
        SkillRevisionRecord(
            id=revision_id,
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            skill_id=skill_id,
            version=1,
            content_digest=package.manifest.content_digest,
            manifest=package.manifest.model_dump(mode="json"),
            imported_from={"kind": "zip", "archive_sha256": "0" * 64},
            created_by_type="user",
            created_by_id=BUILDER_ID,
            created_at=NOW,
        )
    )


def _locks(
    fixture: RuntimeFixture,
    revision_ids: tuple[str, ...] = (DEPLOY_REVISION_ID, REVIEW_REVISION_ID),
) -> tuple[SkillRevisionLock, ...]:
    values = {
        DEPLOY_REVISION_ID: SkillRevisionLock(
            skill_id=DEPLOY_SKILL_ID,
            skill_revision_id=DEPLOY_REVISION_ID,
            skill_key=fixture.deploy.manifest.skill_name,
            version=1,
            content_digest=fixture.deploy.manifest.content_digest,
        ),
        REVIEW_REVISION_ID: SkillRevisionLock(
            skill_id=REVIEW_SKILL_ID,
            skill_revision_id=REVIEW_REVISION_ID,
            skill_key=fixture.review.manifest.skill_name,
            version=1,
            content_digest=fixture.review.manifest.content_digest,
        ),
    }
    return tuple(values[item] for item in revision_ids)


@pytest.mark.anyio
async def test_worker_materializes_every_effective_skill(
    runtime_fixture: RuntimeFixture,
    tmp_path: Path,
) -> None:
    locks = _locks(runtime_fixture, (REVIEW_REVISION_ID, DEPLOY_REVISION_ID))
    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        locks=locks,
    )

    assert runtime.manager is not None
    assert tuple(item.name for item in await runtime.manager.scan(files=_files(tmp_path))) == ("deploy", "review")
    assert runtime.materialization_root is not None
    assert runtime.materialization_root.startswith("/environment/workspace/.a13n/skills/version-1/")
    root = tmp_path / runtime.materialization_root.lstrip("/")
    assert (root / runtime_fixture.review.manifest.content_digest / "checklist.md").read_bytes() == b"# Checklist\n"
    assert (root / runtime_fixture.deploy.manifest.content_digest / "scripts/deploy.sh").is_file()
    assert (root / ".a13n-service-complete.json").is_file()


@pytest.mark.anyio
async def test_empty_effective_skill_list_needs_no_runtime(runtime_fixture: RuntimeFixture) -> None:
    runtime = await runtime_fixture.runtime.prepare(organization_id=ORG_ID, workspace_id=WORKSPACE_ID, locks=())

    assert runtime.manager is None
    assert runtime.catalog_digest is None
    assert runtime.materialization_root is None


@pytest.mark.anyio
async def test_runtime_rejects_duplicate_exact_locks(runtime_fixture: RuntimeFixture) -> None:
    lock = _locks(runtime_fixture, (DEPLOY_REVISION_ID,))[0]

    with pytest.raises(SkillRuntimeError) as captured:
        await runtime_fixture.runtime.prepare(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            locks=(lock, lock),
        )

    assert captured.value.code == "skill_materialization_invalid"


@pytest.mark.anyio
async def test_materializer_replaces_tampering_and_keeps_manifest_outside_packages(
    runtime_fixture: RuntimeFixture,
    tmp_path: Path,
) -> None:
    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        locks=_locks(runtime_fixture, (DEPLOY_REVISION_ID,)),
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
    assert not (skill_root / ".a13n-service-complete.json").exists()

    (root / ".a13n-service-complete.json").unlink()
    assert tuple(item.name for item in await runtime.manager.scan(files=files)) == ("deploy",)
    assert (root / ".a13n-service-complete.json").is_file()


@pytest.mark.anyio
async def test_materializer_rereads_package_without_retaining_run_lifetime_bytes(
    runtime_fixture: RuntimeFixture,
    tmp_path: Path,
) -> None:
    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        locks=_locks(runtime_fixture, (DEPLOY_REVISION_ID,)),
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
    resolved = _locks(runtime_fixture, (DEPLOY_REVISION_ID,))[0]
    selected = LockedSkillRevision(
        lock=SkillRevisionLock.model_validate(resolved.model_dump(mode="json")),
        manifest=runtime_fixture.deploy.manifest,
    )
    plan = SkillMaterializationPlan(
        target_root="/skills",
        catalog_digest="a" * 64,
        revisions=(selected,),
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
    )
    fence = ExpiringFence(stale_on_call=6)
    materializer = EnvironmentSkillMaterializer(
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
async def test_runtime_keeps_retained_deleted_revision_executable(runtime_fixture: RuntimeFixture) -> None:
    async with transaction(runtime_fixture.sessions) as session:
        skill = await session.get(SkillRecord, DEPLOY_SKILL_ID)
        assert skill is not None
        skill.deleted_at = NOW

    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        locks=_locks(runtime_fixture, (DEPLOY_REVISION_ID,)),
    )

    assert runtime.manager is not None


@pytest.mark.anyio
async def test_runtime_rejects_tampered_lock_and_stale_fence(
    runtime_fixture: RuntimeFixture,
    tmp_path: Path,
) -> None:
    locks = _locks(runtime_fixture, (DEPLOY_REVISION_ID,))
    async with transaction(runtime_fixture.sessions) as session:
        revision = await session.scalar(select(SkillRevisionRecord).where(SkillRevisionRecord.id == DEPLOY_REVISION_ID))
        assert revision is not None
        revision.content_digest = "f" * 64
    with pytest.raises(SkillRuntimeError) as invalid:
        await runtime_fixture.runtime.prepare(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            locks=locks,
        )
    assert invalid.value.code == "skill_materialization_invalid"

    class _StaleFence:
        async def require_current(self) -> None:
            raise SkillMaterializationStale

    with pytest.raises(SkillRuntimeError) as stale:
        await runtime_fixture.runtime.prepare(
            organization_id=ORG_ID,
            workspace_id=WORKSPACE_ID,
            locks=locks,
            fence=_StaleFence(),
        )
    assert stale.value.code == "skill_materialization_stale"

    selected = LockedSkillRevision(
        lock=SkillRevisionLock.model_validate(locks[0].model_dump(mode="json")),
        manifest=runtime_fixture.deploy.manifest,
    )
    source = MaterializedSkillSource(
        "foundation-skills-test",
        SkillMaterializationPlan(
            target_root="/skills",
            catalog_digest="a" * 64,
            revisions=(selected,),
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
