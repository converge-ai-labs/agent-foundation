from __future__ import annotations

import hashlib
import stat
import subprocess
import sys
from pathlib import Path

import anyio
import pytest
from a13n_service.plugins.commands import PluginRuntimeCommandFailure
from a13n_service.plugins.materialization import (
    MaterializedPluginRuntime,
    PluginRuntimeMaterializationError,
    PluginRuntimeMaterializer,
)
from a13n_service.plugins.objects import PLUGIN_WHEEL_CONTENT_TYPE, PluginObjectStore, plugin_artifact_key
from a13n_service.plugins.runner_supervisor import PluginRunnerSupervisor
from a13n_service.plugins.runtime import (
    LockedDistribution,
    LockedPlugin,
    PluginRuntimeLock,
    WorkerReleaseManifest,
    default_runtime_target,
)
from a13n_service.storage.object_store import LocalObjectStore

from .conftest import build_wheel

_VALID_FACTORY = b"""from pydantic import BaseModel
from a13n_harness import AbstractHarnessPlugin
from a13n_harness.plugin_factories import HarnessPluginFactory

class Configuration(BaseModel):
    pass

class Plugin(AbstractHarnessPlugin):
    def __init__(self, plugin_id):
        self._plugin_id = plugin_id

    @property
    def plugin_id(self):
        return self._plugin_id

class Factory(HarnessPluginFactory):
    @classmethod
    def plugin_key(cls):
        return "acme.audit"

    def validate_configuration(self, configuration):
        return Configuration.model_validate(configuration)

    def create_plugin(self, context):
        return Plugin(context.plugin_id)
"""


def _runtime_lock(plugin_wheel: bytes, dependency_wheel: bytes) -> PluginRuntimeLock:
    plugin_digest = hashlib.sha256(plugin_wheel).hexdigest()
    dependency_digest = hashlib.sha256(dependency_wheel).hexdigest()
    runtime_lock = PluginRuntimeLock(
        mode="runner",
        runtime_target=default_runtime_target(),
        worker_release="worker-v1",
        harness_version="harness-v1",
        plugins=(
            LockedPlugin(
                plugin_id="plg_1234567890abcdef",
                plugin_version_id="plgv_1234567890abcdef",
                plugin_key="acme.audit",
                distribution_name="acme-audit",
                version="1.0.0",
                top_level_package="acme_audit",
                wheel_digest=plugin_digest,
            ),
        ),
        distributions=(
            LockedDistribution(
                distribution_name="acme-audit",
                version="1.0.0",
                source="artifact",
                artifact_digest=plugin_digest,
                artifact_ref=plugin_artifact_key(plugin_digest),
            ),
            LockedDistribution(
                distribution_name="dependency-one",
                version="1.0.0",
                source="artifact",
                artifact_digest=dependency_digest,
                artifact_ref=plugin_artifact_key(dependency_digest),
            ),
        ),
        digest="0" * 64,
    )
    return runtime_lock.model_copy(update={"digest": runtime_lock.computed_digest()})


async def _put_wheel(objects: LocalObjectStore, wheel: bytes, *, advertised_digest: str | None = None) -> str:
    digest = advertised_digest or hashlib.sha256(wheel).hexdigest()
    key = plugin_artifact_key(digest)
    await objects.put(
        key,
        wheel,
        content_type=PLUGIN_WHEEL_CONTENT_TYPE,
        metadata={"content-sha256": digest, "size-bytes": str(len(wheel))},
        if_none_match=True,
    )
    return key


async def _materializer(
    tmp_path: Path,
    objects: LocalObjectStore,
    *,
    distributions: dict[str, str] | None = None,
) -> PluginRuntimeMaterializer:
    return await PluginRuntimeMaterializer.create(
        tmp_path / "files",
        PluginObjectStore(objects),
        WorkerReleaseManifest(
            worker_release="current-worker",
            harness_version="harness-v1",
            runtime_target=default_runtime_target(),
            distributions=distributions or {},
        ),
        max_wheel_bytes=1024 * 1024,
        max_expanded_bytes=2 * 1024 * 1024,
        max_archive_members=100,
        max_runtime_bytes=8 * 1024 * 1024,
        timeout_seconds=30,
    )


@pytest.mark.anyio
async def test_materializes_exact_runtime_atomically_and_replays(tmp_path: Path) -> None:
    plugin_wheel = build_wheel(
        requires_dist=("dependency-one==1.0.0",),
        factory_source=_VALID_FACTORY,
    )
    dependency_wheel = build_wheel(
        distribution_name="dependency-one",
        package="dependency_one",
        include_entry_point=False,
    )
    runtime_lock = _runtime_lock(plugin_wheel, dependency_wheel)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_wheel(objects, plugin_wheel)
    await _put_wheel(objects, dependency_wheel)
    materializer = await _materializer(tmp_path, objects)

    first = await materializer.materialize(runtime_lock)
    replay = await materializer.materialize(runtime_lock)

    assert replay == first
    assert (first.site_packages / "acme_audit" / "__init__.py").is_file()
    assert (first.site_packages / "dependency_one" / "__init__.py").is_file()
    assert first.manifest_path.is_file()
    assert not (first.root.stat().st_mode & stat.S_IWUSR)
    assert not (first.manifest_path.stat().st_mode & stat.S_IWUSR)
    completed = await anyio.run_process(
        [
            sys.executable,
            "-m",
            "a13n_service.plugins.runner_bootstrap",
            str(first.root),
            runtime_lock.digest,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert completed.returncode == 0


@pytest.mark.anyio
async def test_concurrent_materialization_reuses_one_complete_directory(tmp_path: Path) -> None:
    plugin_wheel = build_wheel(requires_dist=("dependency-one==1.0.0",))
    dependency_wheel = build_wheel(
        distribution_name="dependency-one",
        package="dependency_one",
        include_entry_point=False,
    )
    runtime_lock = _runtime_lock(plugin_wheel, dependency_wheel)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_wheel(objects, plugin_wheel)
    await _put_wheel(objects, dependency_wheel)
    materializer = await _materializer(tmp_path, objects)

    results: list[MaterializedPluginRuntime] = []

    async def run() -> None:
        results.append(await materializer.materialize(runtime_lock))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(run)
        tasks.start_soon(run)

    assert len(results) == 2
    assert results[0] == results[1]


@pytest.mark.anyio
async def test_materialization_rejects_object_bytes_that_do_not_match_lock(tmp_path: Path) -> None:
    plugin_wheel = build_wheel(requires_dist=("dependency-one==1.0.0",))
    dependency_wheel = build_wheel(
        distribution_name="dependency-one",
        package="dependency_one",
        include_entry_point=False,
    )
    runtime_lock = _runtime_lock(plugin_wheel, dependency_wheel)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_wheel(objects, plugin_wheel)
    expected_digest = hashlib.sha256(dependency_wheel).hexdigest()
    corrupted = dependency_wheel[:-1] + bytes([dependency_wheel[-1] ^ 1])
    await _put_wheel(objects, corrupted, advertised_digest=expected_digest)
    materializer = await _materializer(tmp_path, objects)

    with pytest.raises(PluginRuntimeMaterializationError, match="plugin_artifact_invalid"):
        await materializer.materialize(runtime_lock)


@pytest.mark.anyio
async def test_materialization_rejects_incompatible_worker_release_dependency(tmp_path: Path) -> None:
    runtime_lock = PluginRuntimeLock(
        mode="runner",
        runtime_target=default_runtime_target(),
        worker_release="worker-v1",
        harness_version="harness-v1",
        distributions=(
            LockedDistribution(
                distribution_name="pydantic",
                version="2.0.0",
                source="worker_release",
            ),
        ),
        digest="0" * 64,
    )
    runtime_lock = runtime_lock.model_copy(update={"digest": runtime_lock.computed_digest()})
    objects = await LocalObjectStore.create(tmp_path / "objects")
    materializer = await _materializer(tmp_path, objects, distributions={"pydantic": "2.1.0"})

    with pytest.raises(PluginRuntimeMaterializationError, match="plugin_worker_dependency_missing"):
        await materializer.materialize(runtime_lock)


@pytest.mark.anyio
async def test_supervisor_stages_and_activates_fresh_runner_process(tmp_path: Path) -> None:
    plugin_wheel = build_wheel(
        requires_dist=("dependency-one==1.0.0",),
        factory_source=_VALID_FACTORY,
    )
    dependency_wheel = build_wheel(
        distribution_name="dependency-one",
        package="dependency_one",
        include_entry_point=False,
    )
    runtime_lock = _runtime_lock(plugin_wheel, dependency_wheel)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_wheel(objects, plugin_wheel)
    await _put_wheel(objects, dependency_wheel)
    materializer = await _materializer(tmp_path, objects)

    async with PluginRunnerSupervisor(
        materializer,
        ready_timeout_seconds=10,
        command_timeout_seconds=5,
        shutdown_timeout_seconds=5,
    ) as supervisor:
        token = await supervisor.stage_candidate(operation_id="op_stage1234567890", runtime_lock=runtime_lock)
        replay = await supervisor.stage_candidate(operation_id="op_stage1234567890", runtime_lock=runtime_lock)

        assert replay == token
        assert supervisor.runtime_lock_digests == (runtime_lock.digest,)
        assert supervisor.catalog_active_digest is None

        await supervisor.activate_candidate(
            operation_id="op_stage1234567890",
            runtime_lock=runtime_lock,
            staging_token=token,
            runtime_version=2,
        )
        await supervisor.activate_candidate(
            operation_id="op_stage1234567890",
            runtime_lock=runtime_lock,
            staging_token=token,
            runtime_version=2,
        )

        assert supervisor.catalog_active_digest == runtime_lock.digest


@pytest.mark.anyio
async def test_supervisor_rejects_candidate_with_invalid_factory(tmp_path: Path) -> None:
    plugin_wheel = build_wheel()
    dependency_wheel = build_wheel(
        distribution_name="dependency-one",
        package="dependency_one",
        include_entry_point=False,
    )
    runtime_lock = _runtime_lock(plugin_wheel, dependency_wheel)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_wheel(objects, plugin_wheel)
    await _put_wheel(objects, dependency_wheel)
    materializer = await _materializer(tmp_path, objects)

    async with PluginRunnerSupervisor(
        materializer,
        ready_timeout_seconds=10,
        command_timeout_seconds=5,
        shutdown_timeout_seconds=5,
    ) as supervisor:
        with pytest.raises(PluginRuntimeCommandFailure) as failed:
            await supervisor.stage_candidate(operation_id="op_failed123456789", runtime_lock=runtime_lock)

        assert failed.value.failure.code == "plugin_runtime_staging_failed"
        assert supervisor.runtime_lock_digests == ()


@pytest.mark.anyio
async def test_supervisor_aborts_uncommitted_candidate(tmp_path: Path) -> None:
    plugin_wheel = build_wheel(factory_source=_VALID_FACTORY)
    dependency_wheel = build_wheel(
        distribution_name="dependency-one",
        package="dependency_one",
        include_entry_point=False,
    )
    runtime_lock = _runtime_lock(plugin_wheel, dependency_wheel)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_wheel(objects, plugin_wheel)
    await _put_wheel(objects, dependency_wheel)
    materializer = await _materializer(tmp_path, objects)

    async with PluginRunnerSupervisor(materializer, ready_timeout_seconds=10) as supervisor:
        token = await supervisor.stage_candidate(operation_id="op_abort123456789", runtime_lock=runtime_lock)

        await supervisor.abort_candidate(
            operation_id="op_abort123456789",
            runtime_lock=runtime_lock,
            staging_token=token,
        )

        assert supervisor.runtime_lock_digests == ()
        assert supervisor.catalog_active_digest is None


@pytest.mark.anyio
async def test_supervisor_recovers_committed_activation_after_restart(tmp_path: Path) -> None:
    plugin_wheel = build_wheel(factory_source=_VALID_FACTORY)
    dependency_wheel = build_wheel(
        distribution_name="dependency-one",
        package="dependency_one",
        include_entry_point=False,
    )
    runtime_lock = _runtime_lock(plugin_wheel, dependency_wheel)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_wheel(objects, plugin_wheel)
    await _put_wheel(objects, dependency_wheel)
    materializer = await _materializer(tmp_path, objects)

    async with PluginRunnerSupervisor(materializer, ready_timeout_seconds=10) as first:
        token = await first.stage_candidate(operation_id="op_recover12345678", runtime_lock=runtime_lock)

    async with PluginRunnerSupervisor(materializer, ready_timeout_seconds=10) as restarted:
        await restarted.activate_candidate(
            operation_id="op_recover12345678",
            runtime_lock=runtime_lock,
            staging_token=token,
            runtime_version=7,
        )

        assert restarted.runtime_lock_digests == (runtime_lock.digest,)
        assert restarted.catalog_active_digest == runtime_lock.digest


@pytest.mark.anyio
async def test_supervisor_rejects_candidate_when_process_capacity_is_exhausted(tmp_path: Path) -> None:
    dependency_wheel = build_wheel(
        distribution_name="dependency-one",
        package="dependency_one",
        include_entry_point=False,
    )
    first_wheel = build_wheel(factory_source=_VALID_FACTORY)
    second_wheel = build_wheel(factory_source=_VALID_FACTORY + b"\nSECOND_BUILD = True\n")
    first_lock = _runtime_lock(first_wheel, dependency_wheel)
    second_lock = _runtime_lock(second_wheel, dependency_wheel)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_wheel(objects, first_wheel)
    await _put_wheel(objects, second_wheel)
    await _put_wheel(objects, dependency_wheel)
    materializer = await _materializer(tmp_path, objects)

    async with PluginRunnerSupervisor(materializer, ready_timeout_seconds=10, max_processes=1) as supervisor:
        await supervisor.stage_candidate(operation_id="op_capacity1234567", runtime_lock=first_lock)

        with pytest.raises(PluginRuntimeCommandFailure) as failed:
            await supervisor.stage_candidate(operation_id="op_capacity2345678", runtime_lock=second_lock)

        assert failed.value.failure.code == "plugin_runtime_capacity_exceeded"
        assert supervisor.runtime_lock_digests == (first_lock.digest,)
