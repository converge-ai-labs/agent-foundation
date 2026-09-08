from __future__ import annotations

import hashlib
import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import anyio
import pytest
from a13n_service.plugins.materialization import PluginRuntimeMaterializer
from a13n_service.plugins.objects import PLUGIN_WHEEL_CONTENT_TYPE, PluginObjectStore, plugin_artifact_key
from a13n_service.plugins.on_demand import (
    OnDemandPluginRuntime,
    OnDemandPluginRuntimeDeclined,
    OnDemandPluginRuntimeFatalError,
)
from a13n_service.plugins.runtime import (
    LockedDistribution,
    LockedPlugin,
    PluginRuntimeLock,
    WorkerReleaseManifest,
    default_runtime_target,
)
from a13n_service.storage.object_store import LocalObjectStore

from .conftest import build_wheel


@pytest.fixture(autouse=True)
def _restore_import_state() -> Iterator[None]:
    original_path = list(sys.path)
    original_modules = set(sys.modules)
    yield
    sys.path[:] = original_path
    for name in set(sys.modules) - original_modules:
        if name.startswith("od_plugin_"):
            sys.modules.pop(name, None)
    importlib.invalidate_caches()


def _factory_source(plugin_key: str) -> bytes:
    return f"""from pydantic import BaseModel
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
        return {plugin_key!r}

    def validate_configuration(self, configuration):
        return Configuration.model_validate(configuration)

    def create_plugin(self, context):
        return Plugin(context.plugin_id)
""".encode()


def _plugin(
    *,
    plugin_key: str,
    distribution_name: str,
    package: str,
    version: str = "1.0.0",
    factory_source: bytes | None = None,
) -> tuple[LockedPlugin, LockedDistribution, bytes]:
    wheel = build_wheel(
        plugin_key=plugin_key,
        distribution_name=distribution_name,
        package=package,
        version=version,
        factory_source=factory_source or _factory_source(plugin_key),
    )
    digest = hashlib.sha256(wheel).hexdigest()
    suffix = hashlib.sha256(f"{plugin_key}:{version}".encode()).hexdigest()[:16]
    locked = LockedPlugin(
        plugin_id=f"plg_{suffix}",
        plugin_version_id=f"plgv_{suffix}",
        plugin_key=plugin_key,
        distribution_name=distribution_name,
        version=version,
        top_level_package=package,
        wheel_digest=digest,
    )
    distribution = LockedDistribution(
        distribution_name=distribution_name,
        version=version,
        source="artifact",
        artifact_digest=digest,
        artifact_ref=plugin_artifact_key(digest),
    )
    return locked, distribution, wheel


def _runtime_lock(
    *items: tuple[LockedPlugin, LockedDistribution, bytes],
    mode: str = "on_demand",
) -> PluginRuntimeLock:
    runtime_lock = PluginRuntimeLock(
        mode=mode,  # type: ignore[arg-type]
        runtime_target=default_runtime_target(),
        worker_release="worker-v1",
        harness_version="harness-v1",
        plugins=tuple(item[0] for item in items),
        distributions=tuple(item[1] for item in items),
        digest="0" * 64,
    )
    return runtime_lock.model_copy(update={"digest": runtime_lock.computed_digest()})


async def _put_plugins(objects: LocalObjectStore, *items: tuple[LockedPlugin, LockedDistribution, bytes]) -> None:
    for _plugin_lock, distribution, wheel in items:
        assert distribution.artifact_ref is not None and distribution.artifact_digest is not None
        await objects.put(
            distribution.artifact_ref,
            wheel,
            content_type=PLUGIN_WHEEL_CONTENT_TYPE,
            metadata={"content-sha256": distribution.artifact_digest, "size-bytes": str(len(wheel))},
            if_none_match=True,
        )


async def _runtime(tmp_path: Path, objects: LocalObjectStore) -> OnDemandPluginRuntime:
    materializer = await PluginRuntimeMaterializer.create(
        tmp_path / "files",
        PluginObjectStore(objects),
        WorkerReleaseManifest(
            worker_release="worker-v1",
            harness_version="harness-v1",
            runtime_target=default_runtime_target(),
            distributions={},
        ),
        max_wheel_bytes=1024 * 1024,
        max_expanded_bytes=2 * 1024 * 1024,
        max_archive_members=100,
        max_runtime_bytes=8 * 1024 * 1024,
        timeout_seconds=30,
    )
    return OnDemandPluginRuntime(materializer)


@pytest.mark.anyio
async def test_loads_exact_factory_once_and_reuses_it(tmp_path: Path) -> None:
    audit = _plugin(
        plugin_key="od.audit",
        distribution_name="od-plugin-audit",
        package="od_plugin_audit",
    )
    runtime_lock = _runtime_lock(audit)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_plugins(objects, audit)
    runtime = await _runtime(tmp_path, objects)

    first = await runtime.prepare_for_claim(runtime_lock)
    replay = await runtime.prepare_for_claim(runtime_lock)

    assert first.runtime_lock_digest == runtime_lock.digest
    assert tuple(first.factory_catalog) == ("od.audit",)
    assert replay.factory_catalog.require("od.audit") is first.factory_catalog.require("od.audit")
    assert [item.plugin_version_id for item in runtime.loaded_plugins] == [audit[0].plugin_version_id]
    assert len(runtime.published_paths) == 1
    assert runtime.ready


@pytest.mark.anyio
async def test_accumulates_compatible_plugins_across_locks(tmp_path: Path) -> None:
    audit = _plugin(
        plugin_key="od.audit",
        distribution_name="od-plugin-audit",
        package="od_plugin_audit",
    )
    metrics = _plugin(
        plugin_key="od.metrics",
        distribution_name="od-plugin-metrics",
        package="od_plugin_metrics",
    )
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_plugins(objects, audit, metrics)
    runtime = await _runtime(tmp_path, objects)

    first = await runtime.prepare_for_claim(_runtime_lock(audit))
    combined = await runtime.prepare_for_claim(_runtime_lock(audit, metrics))

    assert tuple(combined.factory_catalog) == ("od.audit", "od.metrics")
    assert combined.factory_catalog.require("od.audit") is first.factory_catalog.require("od.audit")
    assert [item.plugin_key for item in runtime.loaded_plugins] == ["od.audit", "od.metrics"]
    assert len(runtime.published_paths) == 2


@pytest.mark.anyio
async def test_concurrent_exact_preflight_publishes_once(tmp_path: Path) -> None:
    audit = _plugin(
        plugin_key="od.concurrent",
        distribution_name="od-plugin-concurrent",
        package="od_plugin_concurrent",
    )
    runtime_lock = _runtime_lock(audit)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_plugins(objects, audit)
    runtime = await _runtime(tmp_path, objects)
    results = []

    async def prepare() -> None:
        results.append(await runtime.prepare_for_claim(runtime_lock))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(prepare)
        tasks.start_soon(prepare)

    assert len(results) == 2
    assert results[0].factory_catalog.require("od.concurrent") is results[1].factory_catalog.require("od.concurrent")
    assert len(runtime.loaded_plugins) == 1
    assert len(runtime.published_paths) == 1


@pytest.mark.anyio
async def test_declines_conflicting_version_before_materialization(tmp_path: Path) -> None:
    version_one = _plugin(
        plugin_key="od.audit",
        distribution_name="od-plugin-audit",
        package="od_plugin_audit",
    )
    version_two = _plugin(
        plugin_key="od.audit",
        distribution_name="od-plugin-audit",
        package="od_plugin_audit",
        version="2.0.0",
    )
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_plugins(objects, version_one, version_two)
    runtime = await _runtime(tmp_path, objects)
    await runtime.prepare_for_claim(_runtime_lock(version_one))

    with pytest.raises(OnDemandPluginRuntimeDeclined, match="plugin_runtime_process_conflict"):
        await runtime.prepare_for_claim(_runtime_lock(version_two))

    assert runtime.ready
    assert len(runtime.published_paths) == 1
    locks_root = tmp_path / "files" / "plugin-runtime-cache-v1" / "locks"
    assert len(tuple(locks_root.iterdir())) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("first", "conflicting"),
    (
        (
            _plugin(
                plugin_key="od.distribution-one",
                distribution_name="od-plugin-shared",
                package="od_plugin_distribution_one",
            ),
            _plugin(
                plugin_key="od.distribution-two",
                distribution_name="od-plugin-shared",
                package="od_plugin_distribution_two",
                version="2.0.0",
            ),
        ),
        (
            _plugin(
                plugin_key="od.package-one",
                distribution_name="od-plugin-package-one",
                package="od_plugin_shared_package",
            ),
            _plugin(
                plugin_key="od.package-two",
                distribution_name="od-plugin-package-two",
                package="od_plugin_shared_package",
            ),
        ),
    ),
)
async def test_declines_distribution_and_top_level_package_conflicts(
    tmp_path: Path,
    first: tuple[LockedPlugin, LockedDistribution, bytes],
    conflicting: tuple[LockedPlugin, LockedDistribution, bytes],
) -> None:
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_plugins(objects, first, conflicting)
    runtime = await _runtime(tmp_path, objects)
    await runtime.prepare_for_claim(_runtime_lock(first))

    with pytest.raises(OnDemandPluginRuntimeDeclined, match="plugin_runtime_process_conflict"):
        await runtime.prepare_for_claim(_runtime_lock(conflicting))

    assert runtime.ready
    assert len(runtime.loaded_plugins) == 1
    assert len(runtime.published_paths) == 1


@pytest.mark.anyio
async def test_declines_when_top_level_package_was_imported_outside_registry(tmp_path: Path) -> None:
    audit = _plugin(
        plugin_key="od.preimported",
        distribution_name="od-plugin-preimported",
        package="od_plugin_preimported",
    )
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_plugins(objects, audit)
    runtime = await _runtime(tmp_path, objects)
    sys.modules["od_plugin_preimported"] = ModuleType("od_plugin_preimported")

    with pytest.raises(OnDemandPluginRuntimeDeclined, match="plugin_runtime_process_conflict"):
        await runtime.prepare_for_claim(_runtime_lock(audit))

    assert runtime.ready
    assert runtime.published_paths == ()


@pytest.mark.anyio
async def test_materialization_failure_leaves_process_ready(tmp_path: Path) -> None:
    audit = _plugin(
        plugin_key="od.missing",
        distribution_name="od-plugin-missing",
        package="od_plugin_missing",
    )
    objects = await LocalObjectStore.create(tmp_path / "objects")
    runtime = await _runtime(tmp_path, objects)

    with pytest.raises(OnDemandPluginRuntimeDeclined, match="plugin_artifact_unavailable"):
        await runtime.prepare_for_claim(_runtime_lock(audit))

    assert runtime.ready
    assert runtime.published_paths == ()


@pytest.mark.anyio
async def test_factory_failure_poison_process_after_path_publication(tmp_path: Path) -> None:
    invalid = _plugin(
        plugin_key="od.invalid",
        distribution_name="od-plugin-invalid",
        package="od_plugin_invalid",
        factory_source=b"class Factory:\n    pass\n",
    )
    runtime_lock = _runtime_lock(invalid)
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_plugins(objects, invalid)
    runtime = await _runtime(tmp_path, objects)

    with pytest.raises(OnDemandPluginRuntimeFatalError, match="plugin_factory_load_failed"):
        await runtime.prepare_for_claim(runtime_lock)

    assert not runtime.ready
    assert len(runtime.published_paths) == 1
    assert str(runtime.published_paths[0]) in sys.path
    with pytest.raises(OnDemandPluginRuntimeFatalError, match="plugin_factory_load_failed"):
        await runtime.prepare_for_claim(runtime_lock)


@pytest.mark.anyio
async def test_rejects_runner_lock_without_touching_process(tmp_path: Path) -> None:
    audit = _plugin(
        plugin_key="od.wrong-mode",
        distribution_name="od-plugin-wrong-mode",
        package="od_plugin_wrong_mode",
    )
    objects = await LocalObjectStore.create(tmp_path / "objects")
    await _put_plugins(objects, audit)
    runtime = await _runtime(tmp_path, objects)

    with pytest.raises(OnDemandPluginRuntimeDeclined, match="plugin_runtime_mode_mismatch"):
        await runtime.prepare_for_claim(_runtime_lock(audit, mode="runner"))

    assert runtime.ready
    assert runtime.published_paths == ()
