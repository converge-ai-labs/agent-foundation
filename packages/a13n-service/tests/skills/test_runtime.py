from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from a13n_environment.direct_local.files import LocalFileOperator
from a13n_environment.direct_local.provider import _DirectLocalFilePolicy
from a13n_harness.environment.files import FileEntriesResult, FileMutationResult, FileWriteMode, FileWriteResult
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.errors import DefinitionError
from a13n_service.agents.errors import AgentError
from a13n_service.agents.invocation_resolution.skills import validate_retained_skills
from a13n_service.iam.models import OrganizationRecord, WorkspaceRecord
from a13n_service.skills.domain import SkillRevisionLock
from a13n_service.skills.materialization import (
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
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.object_store import LocalObjectStore
from a13n_service.storage.relational import create_session_factory, create_sql_engine
from anyio import Event, create_task_group, fail_after
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


@pytest.fixture
async def runtime_fixture(tmp_path: Path, service_database: PostgreSQLConfig) -> AsyncIterator[RuntimeFixture]:
    engine = create_sql_engine(service_database)
    sessions = create_session_factory(engine)
    deploy = _package("deploy", "Deploy safely.", (("scripts/deploy.sh", b"#!/bin/sh\n"), ("empty.txt", b"")))
    review = _package("review", "Review carefully.", (("checklist.md", b"# Checklist\n"),))
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
            default_revision_id=revision_id,
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
@pytest.mark.parametrize("working_directory", ["/", "/workspace"])
async def test_worker_materializes_every_effective_skill(
    runtime_fixture: RuntimeFixture,
    tmp_path: Path,
    working_directory: str,
) -> None:
    locks = _locks(runtime_fixture, (REVIEW_REVISION_ID, DEPLOY_REVISION_ID))
    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        locks=locks,
        working_directory=working_directory,
    )

    assert runtime.manager is not None
    assert tuple(item.name for item in await runtime.manager.scan(files=_files(tmp_path))) == ("deploy", "review")
    assert runtime.materialization_root is not None
    assert runtime.materialization_root.startswith(
        f"/environment/workspace{working_directory.rstrip('/')}/.a13n/skills/version-1/"
    )
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
@pytest.mark.parametrize(
    "corruption", ["bytes", "size", "extra", "symlink", "directory", "parent", "marker", "staging"]
)
async def test_materializer_rejects_conflicting_content_without_repair(
    runtime_fixture: RuntimeFixture,
    tmp_path: Path,
    corruption: str,
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
    document = skill_root / "SKILL.md"
    if corruption == "bytes":
        document.write_bytes(b"x" * document.stat().st_size)
    elif corruption == "size":
        document.write_bytes(b"truncated")
    elif corruption == "extra":
        (skill_root / "unexpected.txt").write_text("unexpected")
    elif corruption == "symlink":
        document.unlink()
        document.symlink_to(skill_root / "scripts/deploy.sh")
    elif corruption == "directory":
        document.unlink()
        document.mkdir()
    elif corruption == "parent":
        (skill_root / "scripts/deploy.sh").unlink()
        (skill_root / "scripts").rmdir()
        (skill_root / "scripts").write_text("not a directory")
    elif corruption == "marker":
        (root / ".a13n-service-complete.json").write_text("wrong catalog")
    else:
        (root / ".a13n-service-staging").rmdir()
        (root / ".a13n-service-staging").write_text("not a directory")

    def snapshot() -> dict[Path, tuple[int, int, int, int]]:
        return {
            path.relative_to(root): (stat.st_ino, stat.st_mode, stat.st_size, stat.st_mtime_ns)
            for path in root.rglob("*")
            for stat in (path.lstat(),)
        }

    before = snapshot()

    with pytest.raises(DefinitionError) as invalid:
        await runtime.manager.scan(files=files)

    assert invalid.value.code == "skill_materialization_invalid"
    assert invalid.value.retry_hint == "dependency_change"
    assert snapshot() == before


@pytest.mark.anyio
async def test_materializer_completes_missing_content_and_reuses_verified_files(
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
    document = skill_root / "SKILL.md"
    before = (document.stat().st_ino, document.stat().st_mtime_ns)
    (root / ".a13n-service-complete.json").unlink()
    (skill_root / "scripts/deploy.sh").unlink()
    (root / ".a13n-service-staging/abandoned").write_bytes(b"partial private upload")

    assert tuple(item.name for item in await runtime.manager.scan(files=files)) == ("deploy",)
    assert (document.stat().st_ino, document.stat().st_mtime_ns) == before
    assert (skill_root / "scripts/deploy.sh").read_bytes() == b"#!/bin/sh\n"
    assert (root / ".a13n-service-complete.json").is_file()
    assert not (skill_root / ".a13n-service-complete.json").exists()
    assert not (skill_root / ".a13n-service-staging").exists()
    assert (root / ".a13n-service-staging/abandoned").read_bytes() == b"partial private upload"

    # The manager retains locks/manifests, not package bytes. A completely
    # verified Environment copy needs no second download during scanning.
    key = skill_package_object_key(ORG_ID, WORKSPACE_ID, runtime_fixture.deploy.manifest.content_digest)
    await runtime_fixture.objects.put(key, b"unavailable source", content_type="application/zip")
    assert tuple(item.name for item in await runtime.manager.scan(files=files)) == ("deploy",)
    assert (document.stat().st_ino, document.stat().st_mtime_ns) == before


@pytest.mark.anyio
async def test_concurrent_materializers_reconcile_file_and_completion_publication(
    runtime_fixture: RuntimeFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtimes = [
        await runtime_fixture.runtime.prepare(
            organization_id=ORG_ID, workspace_id=WORKSPACE_ID, locks=_locks(runtime_fixture)
        )
        for _ in range(2)
    ]
    original_move = LocalFileOperator.move
    arrivals: dict[str, int] = {}
    ready: dict[str, Event] = {}

    async def concurrent_move(
        self: LocalFileOperator, source: str, destination: str, *, replace: bool = False
    ) -> FileMutationResult:
        assert not replace
        arrivals[destination] = arrivals.get(destination, 0) + 1
        barrier = ready.setdefault(destination, Event())
        if arrivals[destination] == 2:
            barrier.set()
        await barrier.wait()
        return await original_move(self, source, destination, replace=replace)

    monkeypatch.setattr(LocalFileOperator, "move", concurrent_move)

    async def scan(index: int) -> None:
        manager = runtimes[index].manager
        assert manager is not None
        assert tuple(item.name for item in await manager.scan(files=_files(tmp_path))) == ("deploy", "review")

    with fail_after(10):
        async with create_task_group() as group:
            group.start_soon(scan, 0)
            group.start_soon(scan, 1)
    assert all(count == 2 for count in arrivals.values())
    assert any(path.endswith("/.a13n-service-complete.json") for path in arrivals)
    assert not list(tmp_path.rglob(".a13n-write-*"))
    assert not list(tmp_path.glob("**/.a13n-service-staging/*"))


@pytest.mark.anyio
@pytest.mark.parametrize("cancel_first", [False, True])
async def test_other_materializer_finishes_while_a_private_publication_is_paused(
    runtime_fixture: RuntimeFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_first: bool
) -> None:
    first, second = [
        await runtime_fixture.runtime.prepare(
            organization_id=ORG_ID, workspace_id=WORKSPACE_ID, locks=_locks(runtime_fixture)
        )
        for _ in range(2)
    ]
    assert first.manager is not None
    assert second.manager is not None
    staged = Event()
    release = Event()
    original_move = LocalFileOperator.move

    async def paused_move(
        self: LocalFileOperator, source: str, destination: str, *, replace: bool = False
    ) -> FileMutationResult:
        if not staged.is_set():
            staged.set()
            await release.wait()
        return await original_move(self, source, destination, replace=replace)

    monkeypatch.setattr(LocalFileOperator, "move", paused_move)
    with fail_after(10):
        async with create_task_group() as group:

            async def scan_first() -> None:
                assert first.manager is not None
                await first.manager.scan(files=_files(tmp_path))

            group.start_soon(scan_first)
            await staged.wait()
            assert not list(tmp_path.rglob("SKILL.md"))
            assert not list(tmp_path.rglob(".a13n-service-complete.json"))
            assert len(await second.manager.scan(files=_files(tmp_path))) == 2
            if cancel_first:
                group.cancel_scope.cancel()
            else:
                release.set()
    assert not list(tmp_path.glob("**/.a13n-service-staging/*"))
    assert len(await second.manager.scan(files=_files(tmp_path))) == 2


@pytest.mark.anyio
async def test_materializer_checks_attempt_fence_after_staging_before_publication(
    runtime_fixture: RuntimeFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Fence:
        stale = False

        async def require_current(self) -> None:
            if self.stale:
                raise SkillMaterializationStale

    fence = Fence()
    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID, workspace_id=WORKSPACE_ID, locks=_locks(runtime_fixture), fence=fence
    )
    original_write = LocalFileOperator.write_bytes_stream

    async def expiring_write(
        self: LocalFileOperator, path: str, chunks: AsyncIterable[bytes], *, mode: FileWriteMode = "overwrite"
    ) -> FileWriteResult:
        result = await original_write(self, path, chunks, mode=mode)
        fence.stale = True
        return result

    monkeypatch.setattr(LocalFileOperator, "write_bytes_stream", expiring_write)
    assert runtime.manager is not None
    with pytest.raises(DefinitionError) as stale:
        await runtime.manager.scan(files=_files(tmp_path))
    assert stale.value.code == "skill_materialization_stale"
    assert not list(tmp_path.rglob("SKILL.md"))
    assert not list(tmp_path.rglob(".a13n-service-complete.json"))
    assert not list(tmp_path.glob("**/.a13n-service-staging/*"))


@pytest.mark.anyio
@pytest.mark.parametrize("ancestor_conflict", [False, True])
async def test_materializer_accepts_concurrent_directory_creation(
    runtime_fixture: RuntimeFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ancestor_conflict: bool
) -> None:
    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID, workspace_id=WORKSPACE_ID, locks=_locks(runtime_fixture)
    )
    original_mkdir = LocalFileOperator.mkdir

    async def raced_mkdir(
        self: LocalFileOperator, path: str, *, parents: bool = False, exist_ok: bool = False
    ) -> FileMutationResult:
        if ancestor_conflict:
            native = tmp_path / path.lstrip("/")
            for candidate in reversed((native, *native.parents)):
                if candidate.is_relative_to(tmp_path) and not candidate.exists():
                    path = "/" + candidate.relative_to(tmp_path).as_posix()
                    break
        await original_mkdir(self, path, parents=parents, exist_ok=exist_ok)
        raise EnvironmentError("Another writer created the directory.", code="environment_conflict")

    monkeypatch.setattr(LocalFileOperator, "mkdir", raced_mkdir)
    assert runtime.manager is not None
    assert len(await runtime.manager.scan(files=_files(tmp_path))) == 2


@pytest.mark.anyio
async def test_materializer_bounds_directory_conflicts_without_progress(
    runtime_fixture: RuntimeFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID, workspace_id=WORKSPACE_ID, locks=_locks(runtime_fixture)
    )
    calls = 0

    async def conflicting_mkdir(
        self: LocalFileOperator, path: str, *, parents: bool = False, exist_ok: bool = False
    ) -> FileMutationResult:
        nonlocal calls
        calls += 1
        raise EnvironmentError("Conflicting directory creation.", code="environment_conflict")

    monkeypatch.setattr(LocalFileOperator, "mkdir", conflicting_mkdir)
    assert runtime.manager is not None
    assert runtime.materialization_root is not None
    with fail_after(5), pytest.raises(DefinitionError) as unavailable:
        await runtime.manager.scan(files=_files(tmp_path))
    assert unavailable.value.code == "skill_materialization_unavailable"
    assert unavailable.value.retry_hint == "new_run"
    assert 1 < calls <= len(Path(runtime.materialization_root).parts) + 1
    assert not list(tmp_path.rglob(".a13n-service-complete.json"))


@pytest.mark.anyio
async def test_new_materializer_recovers_from_unknown_file_publication_outcome(
    runtime_fixture: RuntimeFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, replacement = [
        await runtime_fixture.runtime.prepare(
            organization_id=ORG_ID, workspace_id=WORKSPACE_ID, locks=_locks(runtime_fixture)
        )
        for _ in range(2)
    ]
    original_move = LocalFileOperator.move

    async def lost_response(
        self: LocalFileOperator, source: str, destination: str, *, replace: bool = False
    ) -> FileMutationResult:
        await original_move(self, source, destination, replace=replace)
        raise EnvironmentError("Publication response was lost.", code="environment_unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(LocalFileOperator, "move", lost_response)
        assert first.manager is not None
        with pytest.raises(DefinitionError) as unavailable:
            await first.manager.scan(files=_files(tmp_path))
    assert unavailable.value.code == "skill_materialization_unavailable"
    assert unavailable.value.retry_hint == "new_run"
    assert len(list(tmp_path.rglob("SKILL.md"))) == 1
    assert not list(tmp_path.rglob(".a13n-service-complete.json"))
    assert replacement.manager is not None
    assert len(await replacement.manager.scan(files=_files(tmp_path))) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("conflict_path", ["SKILL.md", "empty.txt"])
async def test_materializer_does_not_overwrite_a_conflicting_publication_winner(
    runtime_fixture: RuntimeFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflict_path: str
) -> None:
    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID, workspace_id=WORKSPACE_ID, locks=_locks(runtime_fixture)
    )
    original_move = LocalFileOperator.move
    conflicting: list[Path] = []

    async def raced_move(
        self: LocalFileOperator, source: str, destination: str, *, replace: bool = False
    ) -> FileMutationResult:
        if destination.endswith(f"/{conflict_path}"):
            path = tmp_path / destination.lstrip("/")
            path.write_bytes(b"conflicting writer")
            conflicting.append(path)
        return await original_move(self, source, destination, replace=replace)

    monkeypatch.setattr(LocalFileOperator, "move", raced_move)
    assert runtime.manager is not None
    with pytest.raises(DefinitionError) as invalid:
        await runtime.manager.scan(files=_files(tmp_path))
    assert invalid.value.code == "skill_materialization_invalid"
    assert len(conflicting) == 1
    assert conflicting[0].read_bytes() == b"conflicting writer"
    assert not list(tmp_path.rglob(".a13n-service-complete.json"))
    assert not list(tmp_path.glob("**/.a13n-service-staging/*"))


@pytest.mark.anyio
async def test_materializer_does_not_clean_up_another_writers_staging_file(
    runtime_fixture: RuntimeFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID, workspace_id=WORKSPACE_ID, locks=_locks(runtime_fixture)
    )
    assert runtime.materialization_root is not None
    existing = tmp_path / runtime.materialization_root.lstrip("/") / ".a13n-service-staging" / ("a" * 32)
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"another writer")
    monkeypatch.setattr("a13n_service.skills.materialization.uuid4", lambda: UUID(hex="a" * 32))
    assert runtime.manager is not None
    with pytest.raises(DefinitionError) as unavailable:
        await runtime.manager.scan(files=_files(tmp_path))
    assert unavailable.value.code == "skill_materialization_unavailable"
    assert existing.read_bytes() == b"another writer"
    assert not list(tmp_path.rglob(".a13n-service-complete.json"))


@pytest.mark.anyio
async def test_materializer_tolerates_completion_publication_between_listing_pages(
    runtime_fixture: RuntimeFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID, workspace_id=WORKSPACE_ID, locks=_locks(runtime_fixture)
    )
    assert runtime.manager is not None
    assert runtime.materialization_root is not None
    await runtime.manager.scan(files=_files(tmp_path))
    root = tmp_path / runtime.materialization_root.lstrip("/")
    marker = root / ".a13n-service-complete.json"
    completion = marker.read_bytes()
    marker.unlink()
    original_list = LocalFileOperator.list

    async def paginated_list(
        self: LocalFileOperator, path: str, *, offset: int = 0, max_results: int = 200, include_hidden: bool = False
    ) -> FileEntriesResult:
        if path == runtime.materialization_root:
            max_results = 1
            if offset == 1:
                # The marker sorts before the previous page, so its publication
                # makes that entry appear again on this offset-based page.
                marker.write_bytes(completion)
        return await original_list(self, path, offset=offset, max_results=max_results, include_hidden=include_hidden)

    monkeypatch.setattr(LocalFileOperator, "list", paginated_list)
    assert len(await runtime.manager.scan(files=_files(tmp_path))) == 2


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
async def test_deletion_blocks_successors_but_keeps_accepted_runtime_executable(
    runtime_fixture: RuntimeFixture,
) -> None:
    locks = _locks(runtime_fixture, (DEPLOY_REVISION_ID,))
    async with transaction(runtime_fixture.sessions) as session:
        await validate_retained_skills(session, organization_id=ORG_ID, workspace_id=WORKSPACE_ID, locks=locks)
    async with transaction(runtime_fixture.sessions) as session:
        skill = await session.get(SkillRecord, DEPLOY_SKILL_ID)
        assert skill is not None
        skill.deleted_at = NOW

    with pytest.raises(AgentError) as rejected:
        async with transaction(runtime_fixture.sessions) as session:
            await validate_retained_skills(session, organization_id=ORG_ID, workspace_id=WORKSPACE_ID, locks=locks)
    assert rejected.value.code == "agent_revision_not_executable"
    assert rejected.value.details == {"reason": "skill_selection_invalid"}

    runtime = await runtime_fixture.runtime.prepare(
        organization_id=ORG_ID,
        workspace_id=WORKSPACE_ID,
        locks=locks,
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
        "service-skills-test",
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
        policy=_DirectLocalFilePolicy(max_value_bytes=64 * 1024 * 1024),
        mount_id="workspace",
        generation="generation-1",
    )
