from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncGenerator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import cast

import a13n_environment_provider.docker.provider as docker_provider_module
import a13n_environment_provider.docker.runtime as docker_runtime_module
import pytest
from a13n_envd_client import EIPSession
from a13n_environment_provider import (
    DEFAULT_DOCKER_IMAGE,
    DirectoryDockerBootstrapStore,
    DockerBootstrapMaterial,
    DockerBootstrapStoreError,
    DockerContainerInspection,
    DockerContainerSpec,
    DockerEngine,
    DockerEngineError,
    DockerEngineMount,
    DockerEnvironmentProvider,
    DockerImageInspection,
    DockerImagePullPolicy,
    DockerMountConfiguration,
    DockerProviderConfiguration,
    DockerProviderRuntime,
    DockerProviderStateData,
    DockerResourcePhase,
    DockerSDKEngine,
    EIPEnvironmentAttachment,
    EIPSessionSource,
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentPauseMode,
    EnvironmentProviderError,
    EnvironmentProviderOutcomeCertainty,
    EnvironmentReconciliationPhase,
    build_environment_provider_factory_catalog,
)

pytestmark = pytest.mark.anyio

_IMAGE_ID = f"sha256:{'1' * 64}"
_CONTAINER_ID = f"sha256:{'2' * 64}"


class _FakeDockerEngine(DockerEngine):
    def __init__(self, *, image_present: bool = False) -> None:
        self.image_present = image_present
        self.pull_count = 0
        self.create_specs: list[DockerContainerSpec] = []
        self.containers: dict[str, DockerContainerInspection] = {}
        self.create_then_error = False
        self.fail_inspect_after_start = False
        self.fail_inspect_after_stop = False
        self.fail_inspect_after_remove = False
        self.status_after_start: str | None = None
        self._inspection_failure_pending = False
        self.topology_validations = 0

    async def validate_local_topology(self) -> None:
        self.topology_validations += 1

    async def inspect_image(self, reference: str) -> DockerImageInspection | None:
        assert reference == DEFAULT_DOCKER_IMAGE
        if not self.image_present:
            return None
        return DockerImageInspection(image_id=_IMAGE_ID, user="sandbox")

    async def pull_image(self, reference: str) -> None:
        assert reference == DEFAULT_DOCKER_IMAGE
        self.pull_count += 1
        self.image_present = True

    async def validate_mount(self, mount: DockerEngineMount) -> None:
        if mount.type == "bind":
            assert Path(mount.source).is_dir()

    async def create_container(self, spec: DockerContainerSpec) -> str:
        self.create_specs.append(spec)
        inspection = DockerContainerInspection(
            container_id=_CONTAINER_ID,
            image_id=spec.image_id,
            status="created",
            user="sandbox",
            command=spec.command,
            environment=spec.environment,
            labels=spec.labels,
            mounts=spec.mounts,
            eip_host_ip="127.0.0.1",
            eip_host_port=49152,
            eip_binding_exact=True,
            eip_route_exact=True,
            nano_cpus=spec.nano_cpus,
            memory_bytes=spec.memory_bytes,
            pids_limit=spec.pids_limit,
        )
        self.containers[_CONTAINER_ID] = inspection
        if self.create_then_error:
            self.create_then_error = False
            raise DockerEngineError("response lost", dispatched=True)
        return _CONTAINER_ID

    async def inspect_container(self, container_id: str) -> DockerContainerInspection | None:
        if self._inspection_failure_pending:
            self._inspection_failure_pending = False
            raise DockerEngineError("inspection response lost")
        return self.containers.get(container_id)

    async def find_containers(self, labels: Mapping[str, str]) -> tuple[DockerContainerInspection, ...]:
        return tuple(
            inspection
            for inspection in self.containers.values()
            if all(inspection.labels.get(key) == value for key, value in labels.items())
        )

    async def start_container(self, container_id: str) -> None:
        self._set_status(container_id, self.status_after_start or "running")
        self.status_after_start = None
        if self.fail_inspect_after_start:
            self.fail_inspect_after_start = False
            self._inspection_failure_pending = True

    async def stop_container(self, container_id: str, *, timeout_seconds: int) -> None:
        assert timeout_seconds >= 0
        self._set_status(container_id, "exited")
        if self.fail_inspect_after_stop:
            self.fail_inspect_after_stop = False
            self._inspection_failure_pending = True

    async def remove_container(self, container_id: str) -> None:
        self.containers.pop(container_id, None)
        if self.fail_inspect_after_remove:
            self.fail_inspect_after_remove = False
            self._inspection_failure_pending = True

    def _set_status(self, container_id: str, status: str) -> None:
        current = self.containers[container_id]
        self.containers[container_id] = DockerContainerInspection(
            container_id=current.container_id,
            image_id=current.image_id,
            status=status,
            user=current.user,
            command=current.command,
            environment=current.environment,
            labels=current.labels,
            mounts=current.mounts,
            eip_host_ip=current.eip_host_ip,
            eip_host_port=current.eip_host_port,
            eip_binding_exact=current.eip_binding_exact,
            eip_route_exact=current.eip_route_exact,
            nano_cpus=current.nano_cpus,
            memory_bytes=current.memory_bytes,
            pids_limit=current.pids_limit,
        )


class _ReadySource(EIPSessionSource):
    def open_session(
        self,
        *,
        expected_environment_id: str,
        required_methods: frozenset[str],
    ) -> AbstractAsyncContextManager[EIPSession]:
        @asynccontextmanager
        async def ready() -> AsyncGenerator[EIPSession]:
            self._claim()
            assert expected_environment_id == "environment-test"
            assert "environment.readiness" in required_methods
            yield cast(EIPSession, object())

        return ready()

    async def discard(self) -> None:
        return None


class _CancelledDiscardSource(_ReadySource):
    async def discard(self) -> None:
        raise asyncio.CancelledError("cancel attachment release")


def _operation(
    action: EnvironmentManagementAction,
    *,
    operation_id: str | None = None,
    attempt: int = 1,
) -> EnvironmentOperationContext:
    return EnvironmentOperationContext(
        operation_id=operation_id or f"operation-{action.value}",
        action=action,
        resource_correlation="resource-test",
        attempt=attempt,
    )


def _provider(tmp_path: Path, engine: _FakeDockerEngine) -> DockerEnvironmentProvider:
    runtime = DockerProviderRuntime(
        engine=engine,
        bootstrap_store=DirectoryDockerBootstrapStore((tmp_path / "bootstrap").resolve()),
    )
    return DockerEnvironmentProvider(DockerProviderConfiguration(environment_id="environment-test"), runtime)


def _patch_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        docker_provider_module,
        "_http_source",
        lambda endpoint, credential: _ReadySource(),
    )


async def test_default_configuration_pulls_sandbox_and_creates_fixed_eip_container(tmp_path: Path) -> None:
    engine = _FakeDockerEngine()
    provider = _provider(tmp_path, engine)

    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE))

    assert engine.pull_count == 1
    assert len(engine.create_specs) == 1
    spec = engine.create_specs[0]
    assert spec.image_id == _IMAGE_ID
    assert spec.command == ("agent-envd", "--config", "/run/a13n/bootstrap/envd.json")
    assert spec.environment["AGENT_ENVD_HTTP_BIND"] == "0.0.0.0:8787"
    assert spec.environment["AGENT_ENVD_HTTP_PLAINTEXT_SCOPE"] == "provider_private_link"
    assert spec.mounts[-1].target == "/run/a13n/bootstrap"
    assert spec.mounts[-1].read_only
    state = DockerProviderStateData.model_validate(resource.state.data)
    assert state.phase is DockerResourcePhase.RUNNING
    assert state.image_id == _IMAGE_ID
    assert state.container_id == _CONTAINER_ID


async def test_never_pull_requires_local_image(tmp_path: Path) -> None:
    engine = _FakeDockerEngine()
    runtime = DockerProviderRuntime(
        engine=engine,
        bootstrap_store=DirectoryDockerBootstrapStore((tmp_path / "bootstrap").resolve()),
    )
    provider = DockerEnvironmentProvider(
        DockerProviderConfiguration(
            environment_id="environment-test",
            pull_policy=DockerImagePullPolicy.NEVER,
        ),
        runtime,
    )

    with pytest.raises(EnvironmentProviderError) as captured:
        await provider.create(operation=_operation(EnvironmentManagementAction.CREATE))

    assert captured.value.code == "provider_resource_missing"
    assert engine.pull_count == 0
    assert not engine.create_specs


async def test_resource_entry_attachment_pause_resume_and_destroy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_readiness(monkeypatch)
    engine = _FakeDockerEngine(image_present=True)
    provider = _provider(tmp_path, engine)
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE))

    async with resource:
        async with resource.acquire_attachment() as attachment:
            assert isinstance(attachment, EIPEnvironmentAttachment)
            assert attachment.environment_id == "environment-test"
        paused = await provider.pause(
            resource,
            operation=_operation(EnvironmentManagementAction.PAUSE),
            mode=EnvironmentPauseMode.FILESYSTEM,
        )

    paused_data = DockerProviderStateData.model_validate(paused.data)
    assert paused_data.phase is DockerResourcePhase.PAUSED
    old_allocation = await provider._runtime.bootstrap_store.recover(paused_data.bootstrap_correlation)
    assert old_allocation is not None
    old_credential = old_allocation.material.credential

    resumed = await provider.resume(paused, operation=_operation(EnvironmentManagementAction.RESUME))
    resumed_data = DockerProviderStateData.model_validate(resumed.state.data)
    assert resumed_data.phase is DockerResourcePhase.RUNNING
    new_allocation = await provider._runtime.bootstrap_store.recover(resumed_data.bootstrap_correlation)
    assert new_allocation is not None
    assert new_allocation.material.credential != old_credential

    async with resumed:
        pass
    await provider.destroy(resumed.state, operation=_operation(EnvironmentManagementAction.DESTROY))
    assert not engine.containers
    assert await provider._runtime.bootstrap_store.recover(resumed_data.bootstrap_correlation) is None


async def test_uncertain_create_reconciles_created_container_as_paused(tmp_path: Path) -> None:
    engine = _FakeDockerEngine(image_present=True)
    engine.create_then_error = True
    provider = _provider(tmp_path, engine)
    operation = _operation(EnvironmentManagementAction.CREATE)

    with pytest.raises(EnvironmentProviderError) as captured:
        await provider.create(operation=operation)

    assert captured.value.certainty is EnvironmentProviderOutcomeCertainty.UNKNOWN
    result = await provider.reconcile(operation, last_known_state=None)
    assert result.phase is EnvironmentReconciliationPhase.PAUSED
    assert result.state is not None
    state = DockerProviderStateData.model_validate(result.state.data)
    assert state.container_id == _CONTAINER_ID


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/run",
        "/run/a13n",
        "/run/a13n/bootstrap",
        "/home/sandbox",
        "/home/sandbox/.local/state/agent-envd",
        "/home/sandbox/.local/state/agent-envd/output",
    ],
)
def test_configuration_rejects_mounts_overlapping_provider_runtime_trees(path: str) -> None:
    with pytest.raises(ValueError, match="must not overlap"):
        DockerProviderConfiguration(
            environment_id="environment-test",
            mounts=(
                DockerMountConfiguration(
                    mount_id="workspace",
                    container_path=path,
                ),
            ),
        )


async def test_resume_post_start_inspection_failure_has_unknown_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_readiness(monkeypatch)
    engine = _FakeDockerEngine(image_present=True)
    provider = _provider(tmp_path, engine)
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE))
    async with resource:
        paused = await provider.pause(
            resource,
            operation=_operation(EnvironmentManagementAction.PAUSE),
            mode=EnvironmentPauseMode.FILESYSTEM,
        )

    engine.fail_inspect_after_start = True
    with pytest.raises(EnvironmentProviderError) as captured:
        await provider.resume(paused, operation=_operation(EnvironmentManagementAction.RESUME))

    assert captured.value.certainty is EnvironmentProviderOutcomeCertainty.UNKNOWN


async def test_resume_post_start_non_running_inspection_has_unknown_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_readiness(monkeypatch)
    engine = _FakeDockerEngine(image_present=True)
    provider = _provider(tmp_path, engine)
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE))
    async with resource:
        paused = await provider.pause(
            resource,
            operation=_operation(EnvironmentManagementAction.PAUSE),
            mode=EnvironmentPauseMode.FILESYSTEM,
        )

    engine.status_after_start = "exited"
    with pytest.raises(EnvironmentProviderError) as captured:
        await provider.resume(paused, operation=_operation(EnvironmentManagementAction.RESUME))

    assert captured.value.certainty is EnvironmentProviderOutcomeCertainty.UNKNOWN


async def test_pause_post_stop_inspection_failure_has_unknown_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_readiness(monkeypatch)
    engine = _FakeDockerEngine(image_present=True)
    provider = _provider(tmp_path, engine)
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE))

    async with resource:
        engine.fail_inspect_after_stop = True
        with pytest.raises(EnvironmentProviderError) as captured:
            await provider.pause(
                resource,
                operation=_operation(EnvironmentManagementAction.PAUSE),
                mode=EnvironmentPauseMode.FILESYSTEM,
            )

    assert captured.value.certainty is EnvironmentProviderOutcomeCertainty.UNKNOWN


async def test_pause_pre_dispatch_inspection_failure_keeps_attachment_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_readiness(monkeypatch)
    engine = _FakeDockerEngine(image_present=True)
    provider = _provider(tmp_path, engine)
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE))

    async with resource:
        engine._inspection_failure_pending = True
        with pytest.raises(EnvironmentProviderError) as captured:
            await provider.pause(
                resource,
                operation=_operation(EnvironmentManagementAction.PAUSE),
                mode=EnvironmentPauseMode.FILESYSTEM,
            )
        assert captured.value.certainty is not EnvironmentProviderOutcomeCertainty.UNKNOWN
        async with resource.acquire_attachment() as attachment:
            assert isinstance(attachment, EIPEnvironmentAttachment)


async def test_destroy_post_remove_inspection_failure_has_unknown_outcome(tmp_path: Path) -> None:
    engine = _FakeDockerEngine(image_present=True)
    provider = _provider(tmp_path, engine)
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE))
    engine.fail_inspect_after_remove = True

    with pytest.raises(EnvironmentProviderError) as captured:
        await provider.destroy(resource.state, operation=_operation(EnvironmentManagementAction.DESTROY))

    assert captured.value.certainty is EnvironmentProviderOutcomeCertainty.UNKNOWN


async def test_same_create_retry_reuses_bootstrap_after_authoritative_absence(tmp_path: Path) -> None:
    engine = _FakeDockerEngine(image_present=True)
    provider = _provider(tmp_path, engine)
    operation = _operation(EnvironmentManagementAction.CREATE)
    bootstrap_correlation = docker_provider_module._bootstrap_correlation(operation.operation_id)
    configuration = DockerProviderConfiguration(environment_id="environment-test")
    fingerprint = docker_provider_module._configuration_fingerprint(configuration)
    material = docker_provider_module._new_bootstrap_material(configuration, fingerprint)
    allocation = await provider._runtime.bootstrap_store.create(bootstrap_correlation, material)

    result = await provider.reconcile(operation, last_known_state=None)
    assert result.phase is EnvironmentReconciliationPhase.ABSENT

    retried = await provider.create(operation=operation.model_copy(update={"attempt": 2}))
    current = await provider._runtime.bootstrap_store.recover(bootstrap_correlation)
    assert current is not None
    assert current.material.credential == allocation.material.credential
    assert DockerProviderStateData.model_validate(retried.state.data).phase is DockerResourcePhase.RUNNING


async def test_sdk_container_mutation_settles_before_cancellation_propagates() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingContainers:
        def create(self, **options: object):
            assert options["image"] == _IMAGE_ID
            entered.set()
            assert release.wait(timeout=5)

            class Container:
                id = _CONTAINER_ID

            return Container()

    class Client:
        containers = BlockingContainers()

    engine = DockerSDKEngine(Client())
    spec = DockerContainerSpec(
        image_id=_IMAGE_ID,
        command=("agent-envd",),
        environment={},
        labels={},
        mounts=(),
        eip_container_port=8787,
        nano_cpus=None,
        memory_bytes=None,
        pids_limit=None,
    )
    create_task = asyncio.create_task(engine.create_container(spec))
    assert await asyncio.to_thread(entered.wait, 5)
    create_task.cancel()
    await asyncio.sleep(0)
    assert not create_task.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await create_task


async def test_cancelled_bootstrap_publication_completes_then_cleans_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _FakeDockerEngine(image_present=True)
    store = DirectoryDockerBootstrapStore((tmp_path / "store").resolve())
    provider = DockerEnvironmentProvider(
        DockerProviderConfiguration(environment_id="environment-test"),
        DockerProviderRuntime(engine=engine, bootstrap_store=store),
    )
    published = threading.Event()
    release = threading.Event()
    original_publish = store._publish_new

    def delayed_publish(
        correlation: str,
        material: DockerBootstrapMaterial,
    ) -> docker_runtime_module.DockerBootstrapAllocation:
        allocation = original_publish(correlation, material)
        published.set()
        assert release.wait(timeout=5)
        return allocation

    monkeypatch.setattr(store, "_publish_new", delayed_publish)
    operation = _operation(EnvironmentManagementAction.CREATE)
    create_task = asyncio.create_task(provider.create(operation=operation))
    assert await asyncio.to_thread(published.wait, 5)
    create_task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await create_task

    correlation = docker_provider_module._bootstrap_correlation(operation.operation_id)
    assert await store.recover(correlation) is None
    assert not engine.create_specs


async def test_attachment_release_preserves_cancellation_and_resets_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = iter((_ReadySource(), _CancelledDiscardSource(), _ReadySource()))
    monkeypatch.setattr(
        docker_provider_module,
        "_http_source",
        lambda endpoint, credential: next(sources),
    )
    engine = _FakeDockerEngine(image_present=True)
    provider = _provider(tmp_path, engine)
    resource = await provider.create(operation=_operation(EnvironmentManagementAction.CREATE))

    async with resource:
        with pytest.raises(asyncio.CancelledError):
            async with resource.acquire_attachment():
                pass
        async with resource.acquire_attachment() as attachment:
            assert isinstance(attachment, EIPEnvironmentAttachment)


async def test_sdk_inspection_requires_one_exact_eip_publication() -> None:
    attributes = {
        "Id": _CONTAINER_ID,
        "Image": _IMAGE_ID,
        "Config": {"User": "sandbox", "Cmd": [], "Env": [], "Labels": {}},
        "State": {"Status": "running"},
        "HostConfig": {
            "PortBindings": {
                "8787/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49152"}],
            }
        },
        "NetworkSettings": {
            "Ports": {
                "8787/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49152"}],
            }
        },
        "Mounts": [],
    }
    exact = docker_runtime_module._inspection(attributes)
    assert exact.eip_binding_exact
    assert exact.eip_route_exact

    attributes["HostConfig"]["PortBindings"]["9000/tcp"] = [  # type: ignore[index]
        {"HostIp": "0.0.0.0", "HostPort": "49000"}
    ]
    extra_binding = docker_runtime_module._inspection(attributes)
    assert not extra_binding.eip_binding_exact

    attributes["HostConfig"]["PortBindings"].pop("9000/tcp")  # type: ignore[index, union-attr]
    attributes["NetworkSettings"]["Ports"]["8787/tcp"].append(  # type: ignore[index, union-attr]
        {"HostIp": "0.0.0.0", "HostPort": "49153"}
    )
    duplicate_route = docker_runtime_module._inspection(attributes)
    assert not duplicate_route.eip_route_exact


async def test_directory_bootstrap_store_recovers_committed_credential_after_late_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DirectoryDockerBootstrapStore((tmp_path / "store").resolve())
    correlation = f"bootstrap-{'c' * 24}"
    first = DockerBootstrapMaterial(
        environment_id="environment-test",
        configuration_fingerprint=f"sha256:{'d' * 64}",
        envd_configuration=b"{}",
        credential="first",
    )
    await store.create(correlation, first)
    replacement = DockerBootstrapMaterial(
        environment_id=first.environment_id,
        configuration_fingerprint=first.configuration_fingerprint,
        envd_configuration=first.envd_configuration,
        credential="replacement",
    )
    original_replace = docker_runtime_module.os.replace

    def replace_then_fail(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        raise OSError("simulated failure after atomic credential commit")

    monkeypatch.setattr(docker_runtime_module.os, "replace", replace_then_fail)
    with pytest.raises(DockerBootstrapStoreError, match="could not be replaced"):
        await store.replace(correlation, replacement)
    monkeypatch.setattr(docker_runtime_module.os, "replace", original_replace)

    recovered = await store.recover(correlation)
    assert recovered is not None
    assert recovered.material == replacement


async def test_directory_bootstrap_store_detects_conflict_and_replaces_credential(tmp_path: Path) -> None:
    store = DirectoryDockerBootstrapStore((tmp_path / "store").resolve())
    correlation = f"bootstrap-{'a' * 24}"
    first = DockerBootstrapMaterial(
        environment_id="environment-test",
        configuration_fingerprint=f"sha256:{'b' * 64}",
        envd_configuration=b"{}",
        credential="first",
    )
    same = await store.create(correlation, first)
    assert await store.create(correlation, first) == same

    conflicting = DockerBootstrapMaterial(
        environment_id=first.environment_id,
        configuration_fingerprint=first.configuration_fingerprint,
        envd_configuration=first.envd_configuration,
        credential="different",
    )
    with pytest.raises(Exception, match="different material"):
        await store.create(correlation, conflicting)

    replaced = await store.replace(correlation, conflicting)
    assert replaced.material.credential == "different"
    await store.remove(correlation)
    assert await store.recover(correlation) is None


def test_builtin_catalog_resolves_docker_without_touching_engine() -> None:
    catalog = build_environment_provider_factory_catalog(builtin_keys=("a13n.docker",))

    assert tuple(catalog) == ("a13n.docker",)
    assert catalog["a13n.docker"].configuration_model("1") is DockerProviderConfiguration
