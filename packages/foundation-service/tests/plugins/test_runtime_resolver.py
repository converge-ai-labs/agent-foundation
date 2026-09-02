from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx2
import pytest
from a13n_service.plugins.commands import PluginRuntimeCatalogSnapshot, PluginRuntimeVersionSpec
from a13n_service.plugins.models import (
    PluginRecord,
    PluginRuntimeResolutionRecord,
    PluginRuntimeTaskRecord,
    PluginVersionRecord,
)
from a13n_service.plugins.objects import PluginObjectStore, plugin_artifact_key
from a13n_service.plugins.runtime import (
    LockedDistribution,
    PluginRuntimeLockStore,
    WorkerReleaseManifest,
    default_runtime_target,
)
from a13n_service.plugins.runtime_resolver import (
    FoundationPluginRuntimeCandidateResolver,
    HttpRuntimeDependencyArtifactRetainer,
    ResolvedDependencyWheel,
    UvRuntimeDependencyResolver,
)
from a13n_service.plugins.service import PluginService
from a13n_service.plugins.staging import PluginStaging
from a13n_service.storage import short_session, transaction
from a13n_service.storage.object_store import LocalObjectStore
from packaging.tags import Tag
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .conftest import ADMIN_ID, NOW, ORG_ID, WORKSPACE_ID, build_wheel
from .test_service import _upload


class _DependencyResolver:
    def __init__(self, result: tuple[ResolvedDependencyWheel, ...]) -> None:
        self.result = result
        self.calls: list[tuple[tuple[str, ...], dict[str, str], dict[str, str]]] = []

    async def resolve(
        self,
        *,
        requirements: Sequence[str],
        preferences: Mapping[str, str],
        constraints: Mapping[str, str],
    ) -> tuple[ResolvedDependencyWheel, ...]:
        self.calls.append((tuple(requirements), dict(preferences), dict(constraints)))
        return self.result


class _ArtifactRetainer:
    def __init__(self) -> None:
        self.calls: list[ResolvedDependencyWheel] = []

    async def retain(self, wheel: ResolvedDependencyWheel) -> LockedDistribution:
        self.calls.append(wheel)
        return LockedDistribution(
            distribution_name=wheel.distribution_name,
            version=wheel.version,
            source="artifact",
            artifact_digest=wheel.content_digest,
            artifact_ref=plugin_artifact_key(wheel.content_digest),
        )


def _runtime_locks(*, distributions: Mapping[str, str] | None = None) -> PluginRuntimeLockStore:
    return PluginRuntimeLockStore(
        WorkerReleaseManifest(
            worker_release="test-worker",
            harness_version="test-harness",
            runtime_target=default_runtime_target(),
            distributions=distributions or {},
        ),
        clock=lambda: NOW,
    )


@pytest.mark.anyio
async def test_uv_resolver_preserves_preferences_and_selects_best_compatible_wheel(tmp_path: Path) -> None:
    executable = tmp_path / "fake-uv"
    executable.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import sys

args = sys.argv[1:]
if any("private-secret" in value for value in args):
    raise SystemExit(20)
output = pathlib.Path(args[args.index("--output-file") + 1])
output_format = args[args.index("--format") + 1]
constraints = pathlib.Path(args[args.index("--constraints") + 1]).read_text()
if "pydantic==2.12.0" not in constraints:
    raise SystemExit(21)
if os.environ.get("UV_DEFAULT_INDEX") != "https://user:private-secret@packages.example/simple":
    raise SystemExit(22)
if pathlib.Path(os.environ.get("HOME", "")).resolve() != pathlib.Path.cwd().resolve():
    raise SystemExit(24)
if os.environ.get("UV_NO_CACHE") != "true" or "PIP_INDEX_URL" in os.environ:
    raise SystemExit(25)
if output_format == "requirements.txt":
    if "idna==3.18" not in output.read_text():
        raise SystemExit(23)
    output.write_text("idna==3.19\\n")
else:
    output.write_text('''lock-version = "1.0"
[[packages]]
name = "idna"
version = "3.19"
wheels = [
  { url = "https://files.example/idna-3.19-py3-none-any.whl", size = 10, hashes = { sha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" } },
  { url = "https://files.example/idna-3.19-cp313-cp313-manylinux_2_28_x86_64.whl", size = 11, hashes = { sha256 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" } },
]
''')
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    resolver = UvRuntimeDependencyResolver(
        tmp_path,
        executable=str(executable),
        default_index_url="https://user:private-secret@packages.example/simple",
        compatible_tags=(
            Tag("cp313", "cp313", "manylinux_2_28_x86_64"),
            Tag("py3", "none", "any"),
        ),
    )

    resolved = await resolver.resolve(
        requirements=("idna>=3",),
        preferences={"idna": "3.18"},
        constraints={"pydantic": "2.12.0"},
    )

    assert resolved == (
        ResolvedDependencyWheel(
            distribution_name="idna",
            version="3.19",
            source_url="https://files.example/idna-3.19-cp313-cp313-manylinux_2_28_x86_64.whl",
            filename="idna-3.19-cp313-cp313-manylinux_2_28_x86_64.whl",
            size_bytes=11,
            content_digest="b" * 64,
        ),
    )


@pytest.mark.anyio
async def test_http_retainer_verifies_and_publishes_dependency_wheel(tmp_path: Path) -> None:
    wheel_bytes = build_wheel(
        distribution_name="dependency-one",
        package="dependency_one",
        include_entry_point=False,
    )
    digest = hashlib.sha256(wheel_bytes).hexdigest()

    def respond(request: httpx2.Request) -> httpx2.Response:
        assert request.url == "https://packages.example/dependency_one-1.0.0-py3-none-any.whl"
        assert request.headers["authorization"] == "Basic dXNlcjpwYWNrYWdlLXNlY3JldA=="
        return httpx2.Response(
            200,
            content=wheel_bytes,
            headers={"content-length": str(len(wheel_bytes))},
        )

    objects = await LocalObjectStore.create(tmp_path / "objects")
    staging = await PluginStaging.create(tmp_path / "files")
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        retainer = HttpRuntimeDependencyArtifactRetainer(
            client,
            staging,
            PluginObjectStore(objects),
            max_wheel_bytes=1024 * 1024,
            max_expanded_bytes=2 * 1024 * 1024,
            max_archive_members=100,
            index_urls=("https://user:package-secret@packages.example/simple",),
        )
        retained = await retainer.retain(
            ResolvedDependencyWheel(
                distribution_name="dependency-one",
                version="1.0.0",
                source_url="https://packages.example/dependency_one-1.0.0-py3-none-any.whl",
                filename="dependency_one-1.0.0-py3-none-any.whl",
                size_bytes=len(wheel_bytes),
                content_digest=digest,
            )
        )

    assert retained.artifact_digest == digest
    assert retained.artifact_ref == plugin_artifact_key(digest)
    assert (await objects.stat(plugin_artifact_key(digest))).size == len(wheel_bytes)


@pytest.mark.parametrize("index_url", ("file:///tmp/simple", "packages.example/simple", ""))
def test_uv_resolver_rejects_non_http_package_indexes(tmp_path: Path, index_url: str) -> None:
    with pytest.raises(ValueError, match="package index URL"):
        UvRuntimeDependencyResolver(tmp_path, default_index_url=index_url)


@pytest.mark.anyio
async def test_candidate_resolver_persists_exact_resolution_and_replays_after_acceptance_crash(
    runner_plugin_service: PluginService,
    plugin_sessions: async_sessionmaker[AsyncSession],
) -> None:
    uploaded = await _upload(
        runner_plugin_service,
        build_wheel(requires_dist=("idna>=3",)),
        key="upload-resolver-plugin",
    )
    operation_id = "op_resolver1234567890"
    async with transaction(plugin_sessions) as session:
        plugin = await session.get(PluginRecord, uploaded.version.plugin_id)
        version = await session.get(PluginVersionRecord, uploaded.version.id)
        assert plugin is not None and version is not None
        target = PluginRuntimeVersionSpec(
            plugin=plugin.to_resource(),
            version=version.to_resource(),
            requires_python=version.requires_python,
            wheel_tags=tuple(version.wheel_tags),
            root_is_purelib=version.root_is_purelib,
            entry_point_target=version.entry_point_target,
        )
        session.add(
            PluginRuntimeTaskRecord(
                id=operation_id,
                organization_id=ORG_ID,
                workspace_id=WORKSPACE_ID,
                actor_type="user",
                actor_id=ADMIN_ID,
                command="activate",
                plugin_id=plugin.id,
                plugin_version_id=version.id,
                status="running",
                phase="accepted",
                expected_runtime_generation=None,
                candidate_lock_digest=None,
                staging_token=None,
                committed_runtime_generation=None,
                result_refs=[],
                error=None,
                created_at=NOW,
                updated_at=NOW,
                completed_at=None,
            )
        )
    wheel = ResolvedDependencyWheel(
        distribution_name="idna",
        version="3.19",
        source_url="https://files.example/idna-3.19-py3-none-any.whl",
        filename="idna-3.19-py3-none-any.whl",
        size_bytes=10,
        content_digest="d" * 64,
    )
    dependencies = _DependencyResolver((wheel,))
    artifacts = _ArtifactRetainer()
    resolver = FoundationPluginRuntimeCandidateResolver(
        plugin_sessions,
        _runtime_locks(),
        dependencies,
        artifacts,
        clock=lambda: NOW,
    )
    catalog = PluginRuntimeCatalogSnapshot(
        runtime_generation=1,
        active_lock_digest=None,
        active_versions=(),
        target_plugin=target.plugin,
        target_version=target,
    )

    first = await resolver.resolve_candidate(operation_id=operation_id, command="activate", catalog=catalog)
    replay = await resolver.resolve_candidate(operation_id=operation_id, command="activate", catalog=catalog)

    assert first == replay
    assert tuple((item.distribution_name, item.version) for item in first.distributions) == (
        ("acme-audit", "1.0.0"),
        ("idna", "3.19"),
    )
    assert len(dependencies.calls) == 1
    assert dependencies.calls[0][0] == ("idna>=3",)
    assert artifacts.calls == [wheel]
    async with short_session(plugin_sessions) as session:
        evidence = await session.get(PluginRuntimeResolutionRecord, operation_id)
        assert evidence is not None and evidence.runtime_lock_digest == first.digest
