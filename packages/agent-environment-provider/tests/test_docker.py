from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from stat import S_IMODE

import pytest
from a13n_environment_provider import (
    DEFAULT_DOCKER_IMAGE,
    DirectoryDockerBootstrapStore,
    DockerBootstrapMaterial,
    DockerContainerInspection,
    DockerContainerSpec,
    DockerEngine,
    DockerEngineError,
    DockerEngineMount,
    DockerEnvironment,
    DockerEnvironmentProvider,
    DockerImageInspection,
    DockerProviderConfiguration,
    DockerProviderRuntime,
    DockerProviderStateData,
    EnvironmentProviderError,
    EnvironmentState,
)
from a13n_environment_provider.docker import provider as provider_module

pytestmark = pytest.mark.anyio

_IMAGE_ID = f"sha256:{'1' * 64}"


class _FakeDockerEngine(DockerEngine):
    def __init__(self) -> None:
        self.containers: dict[str, DockerContainerInspection] = {}
        self.create_specs: list[DockerContainerSpec] = []
        self.inspect_error: DockerEngineError | None = None
        self.find_error: DockerEngineError | None = None
        self.start_error: DockerEngineError | None = None
        self.stop_error: DockerEngineError | None = None
        self.remove_error: DockerEngineError | None = None
        self.start_calls = 0
        self.stop_calls = 0
        self.remove_calls = 0
        self._counter = 0

    async def validate_local_topology(self) -> None:
        return None

    async def inspect_image(self, reference: str) -> DockerImageInspection | None:
        assert reference == DEFAULT_DOCKER_IMAGE
        return DockerImageInspection(image_id=_IMAGE_ID, user="sandbox")

    async def pull_image(self, reference: str) -> None:
        raise AssertionError(f"unexpected image pull: {reference}")

    async def validate_mount(self, mount: DockerEngineMount) -> None:
        if mount.type == "bind":
            assert Path(mount.source).exists()

    async def create_container(self, spec: DockerContainerSpec) -> str:
        self._counter += 1
        container_id = f"sha256:{self._counter:064x}"
        self.create_specs.append(spec)
        self.containers[container_id] = self._inspection(container_id, spec, "created")
        return container_id

    async def inspect_container(self, container_id: str) -> DockerContainerInspection | None:
        if self.inspect_error is not None:
            error = self.inspect_error
            self.inspect_error = None
            raise error
        return self.containers.get(container_id)

    async def find_containers(self, labels: Mapping[str, str]) -> tuple[DockerContainerInspection, ...]:
        if self.find_error is not None:
            error = self.find_error
            self.find_error = None
            raise error
        return tuple(
            item
            for item in self.containers.values()
            if all(item.labels.get(key) == value for key, value in labels.items())
        )

    async def start_container(self, container_id: str) -> None:
        self.start_calls += 1
        if self.start_error is not None:
            error = self.start_error
            self.start_error = None
            raise error
        current = self.containers[container_id]
        self.containers[container_id] = self._inspection_from(current, "running")

    async def stop_container(self, container_id: str, *, timeout_seconds: int) -> None:
        del timeout_seconds
        self.stop_calls += 1
        if self.stop_error is not None:
            error = self.stop_error
            self.stop_error = None
            raise error
        current = self.containers[container_id]
        self.containers[container_id] = self._inspection_from(current, "exited")

    async def remove_container(self, container_id: str) -> None:
        self.remove_calls += 1
        if self.remove_error is not None:
            error = self.remove_error
            self.remove_error = None
            raise error
        self.containers.pop(container_id, None)

    @staticmethod
    def _inspection(container_id: str, spec: DockerContainerSpec, status: str) -> DockerContainerInspection:
        return DockerContainerInspection(
            container_id=container_id,
            image_id=spec.image_id,
            status=status,
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

    @staticmethod
    def _inspection_from(current: DockerContainerInspection, status: str) -> DockerContainerInspection:
        return DockerContainerInspection(
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


def _environment(tmp_path: Path, engine: _FakeDockerEngine, state: EnvironmentState | None = None) -> DockerEnvironment:
    provider = DockerEnvironmentProvider()
    configuration = provider.validate_configuration(
        schema_version="1",
        value={},
    )
    environment = provider.create_environment(
        environment_id="environment-test",
        configuration=configuration,
        state=state,
        runtime=DockerProviderRuntime(
            engine=engine,
            bootstrap_store=DirectoryDockerBootstrapStore((tmp_path / "bootstrap").resolve()),
        ),
    )
    assert isinstance(environment, DockerEnvironment)
    return environment


def _skip_eip(monkeypatch: pytest.MonkeyPatch) -> None:
    async def open_eip(self, inspection, allocation, *, mount_id: str) -> None:
        del self, inspection, allocation, mount_id

    monkeypatch.setattr(DockerEnvironment, "_open_eip", open_eip)


def test_docker_bootstrap_uses_current_envd_file_configuration_contract() -> None:
    configuration = DockerProviderConfiguration()

    payload = json.loads(provider_module._envd_configuration(configuration))

    assert set(payload) == {
        "limits",
        "mounts",
        "root_mount_id",
        "shell_profiles",
        "trusted_executable_roots",
    }
    assert payload["mounts"] == [
        {
            "mount_id": "workspace",
            "native_root": "/workspace",
            "writable": True,
            "allow_command_execution": True,
            "max_file_bytes": 16 * 1024 * 1024,
        }
    ]
    assert payload["shell_profiles"] == [
        {
            "profile_id": "bash",
            "display_name": "bash",
            "native_executable": "/bin/bash",
            "fixed_arguments": ["-c"],
            "safe_base_environment": {},
            "executable_search_roots": ["/bin"],
            "max_script_bytes": 1024 * 1024,
            "allow_login_mode": False,
        }
    ]
    assert payload["limits"] == {
        "max_output_preview_bytes": 64 * 1024,
        "max_output_bytes_per_stream": 1024 * 1024 * 1024,
        "max_spool_bytes": 64 * 1024 * 1024 * 1024,
    }
    assert payload["root_mount_id"] == "workspace"
    assert payload["trusted_executable_roots"] == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem modes are required")
async def test_directory_bootstrap_store_keeps_host_root_private_across_replacement(tmp_path: Path) -> None:
    root = (tmp_path / "bootstrap").resolve()
    store = DirectoryDockerBootstrapStore(root)
    initial = DockerBootstrapMaterial(
        environment_id="environment-test",
        configuration_fingerprint=f"sha256:{'2' * 64}",
        envd_configuration=b"{}",
        credential="credential-initial",
    )

    allocation = await store.create("bootstrap-1234567890abcdef12345678", initial)
    assert S_IMODE(root.stat().st_mode) == 0o700
    assert allocation.material.credential == "credential-initial"

    root.chmod(0o755)
    replacement = DockerBootstrapMaterial(
        environment_id=initial.environment_id,
        configuration_fingerprint=initial.configuration_fingerprint,
        envd_configuration=initial.envd_configuration,
        credential="credential-replaced",
    )
    allocation = await store.replace(allocation.correlation, replacement)

    assert S_IMODE(root.stat().st_mode) == 0o700
    assert allocation.material.credential == "credential-replaced"


async def test_docker_create_caches_exact_container_state_before_entry_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _skip_eip(monkeypatch)
    engine = _FakeDockerEngine()
    environment = _environment(tmp_path, engine)

    await environment.enter(
        thread_id="thread-1",
        run_id="run-1",
        agent_instance_id="agent-1",
        mount_id="workspace",
    )
    await environment.prepare()

    state = environment.dump_state()
    assert state is not None
    decoded = DockerProviderStateData.model_validate(state.state)
    assert decoded.container_id in engine.containers
    assert engine.containers[decoded.container_id].status == "running"
    assert len(engine.create_specs) == 1
    assert engine.create_specs[0].environment["AGENT_ENVD_ENVIRONMENT_ID"] == "environment-test"
    await environment.close()
    assert decoded.container_id in engine.containers


async def test_docker_failed_start_retains_created_container_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _skip_eip(monkeypatch)
    engine = _FakeDockerEngine()
    engine.start_error = DockerEngineError("start response lost", dispatched=True)
    environment = _environment(tmp_path, engine)

    with pytest.raises(EnvironmentProviderError):
        await environment.enter(
            thread_id="thread-1",
            run_id="run-1",
            agent_instance_id="agent-1",
            mount_id="workspace",
        )
        await environment.prepare()

    state = environment.dump_state()
    assert state is not None
    assert DockerProviderStateData.model_validate(state.state).container_id in engine.containers
    await environment.close()


async def test_docker_reentry_reuses_exact_target_and_replaces_confirmed_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _skip_eip(monkeypatch)
    engine = _FakeDockerEngine()
    first = _environment(tmp_path, engine)
    await first.enter(
        thread_id="thread-1",
        run_id="run-1",
        agent_instance_id="agent-1",
        mount_id="workspace",
    )
    await first.prepare()
    state = first.dump_state()
    assert state is not None
    first_id = DockerProviderStateData.model_validate(state.state).container_id
    await first.close()

    reentered = _environment(tmp_path, engine, state)
    await reentered.enter(
        thread_id="thread-1",
        run_id="run-2",
        agent_instance_id="agent-2",
        mount_id="workspace",
    )
    await reentered.prepare()
    assert DockerProviderStateData.model_validate(reentered.dump_state().state).container_id == first_id  # type: ignore[union-attr]
    await reentered.close()

    engine.containers.pop(first_id)
    replacement = _environment(tmp_path, engine, state)
    await replacement.enter(
        thread_id="thread-1",
        run_id="run-3",
        agent_instance_id="agent-3",
        mount_id="workspace",
    )
    await replacement.prepare()
    replacement_state = replacement.dump_state()
    assert replacement_state is not None
    assert DockerProviderStateData.model_validate(replacement_state.state).container_id != first_id
    await replacement.close()


async def test_docker_destroy_uses_fresh_adapter_and_clears_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _skip_eip(monkeypatch)
    engine = _FakeDockerEngine()
    entered = _environment(tmp_path, engine)
    await entered.enter(
        thread_id="thread-1",
        run_id="run-1",
        agent_instance_id="agent-1",
        mount_id="workspace",
    )
    await entered.prepare()
    state = entered.dump_state()
    assert state is not None
    container_id = DockerProviderStateData.model_validate(state.state).container_id
    await entered.close()

    destroyer = _environment(tmp_path, engine, state)
    await destroyer.destroy()
    assert container_id not in engine.containers
    assert destroyer.dump_state() is None
    await destroyer.close()


async def test_docker_inspection_unavailable_does_not_create_speculative_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _skip_eip(monkeypatch)
    engine = _FakeDockerEngine()
    first = _environment(tmp_path, engine)
    await first.enter(
        thread_id="thread-1",
        run_id="run-1",
        agent_instance_id="agent-1",
        mount_id="workspace",
    )
    await first.prepare()
    state = first.dump_state()
    assert state is not None
    await first.close()
    initial_create_count = len(engine.create_specs)

    engine.inspect_error = DockerEngineError("inspection unavailable")
    reentry = _environment(tmp_path, engine, state)
    with pytest.raises(EnvironmentProviderError) as captured:
        await reentry.enter(
            thread_id="thread-1",
            run_id="run-2",
            agent_instance_id="agent-2",
            mount_id="workspace",
        )
        await reentry.prepare()

    assert captured.value.code == "provider_unavailable"
    assert len(engine.create_specs) == initial_create_count
    assert reentry.dump_state() == state
    await reentry.close()


async def test_docker_incompatible_target_fails_without_start_or_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _skip_eip(monkeypatch)
    engine = _FakeDockerEngine()
    first = _environment(tmp_path, engine)
    await first.enter(
        thread_id="thread-1",
        run_id="run-1",
        agent_instance_id="agent-1",
        mount_id="workspace",
    )
    await first.prepare()
    state = first.dump_state()
    assert state is not None
    decoded = DockerProviderStateData.model_validate(state.state)
    await first.close()
    current = engine.containers[decoded.container_id]
    engine.containers[decoded.container_id] = DockerContainerInspection(
        container_id=current.container_id,
        image_id=current.image_id,
        status="exited",
        user=current.user,
        command=current.command,
        environment=current.environment,
        labels={**current.labels, "io.a13n.configuration-fingerprint": f"sha256:{'9' * 64}"},
        mounts=current.mounts,
        eip_host_ip=current.eip_host_ip,
        eip_host_port=current.eip_host_port,
        eip_binding_exact=current.eip_binding_exact,
        eip_route_exact=current.eip_route_exact,
        nano_cpus=current.nano_cpus,
        memory_bytes=current.memory_bytes,
        pids_limit=current.pids_limit,
    )
    initial_create_count = len(engine.create_specs)
    initial_start_count = engine.start_calls

    reentry = _environment(tmp_path, engine, state)
    with pytest.raises(EnvironmentProviderError) as captured:
        await reentry.enter(
            thread_id="thread-1",
            run_id="run-2",
            agent_instance_id="agent-2",
            mount_id="workspace",
        )
        await reentry.prepare()

    assert captured.value.code == "provider_state_conflict"
    assert len(engine.create_specs) == initial_create_count
    assert engine.start_calls == initial_start_count
    assert reentry.dump_state() == state
    await reentry.close()


async def test_docker_eip_failure_after_create_retains_known_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _FakeDockerEngine()
    environment = _environment(tmp_path, engine)

    async def fail_eip(self, inspection, allocation, *, mount_id: str) -> None:
        del self, inspection, allocation, mount_id
        raise RuntimeError("EIP readiness failed")

    monkeypatch.setattr(DockerEnvironment, "_open_eip", fail_eip)
    with pytest.raises(RuntimeError, match="EIP readiness failed"):
        await environment.enter(
            thread_id="thread-1",
            run_id="run-1",
            agent_instance_id="agent-1",
            mount_id="workspace",
        )
        await environment.prepare()

    state = environment.dump_state()
    assert state is not None
    assert DockerProviderStateData.model_validate(state.state).container_id in engine.containers
    await environment.close()


async def test_docker_unknown_destroy_outcome_preserves_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _skip_eip(monkeypatch)
    engine = _FakeDockerEngine()
    entered = _environment(tmp_path, engine)
    await entered.enter(
        thread_id="thread-1",
        run_id="run-1",
        agent_instance_id="agent-1",
        mount_id="workspace",
    )
    await entered.prepare()
    state = entered.dump_state()
    assert state is not None
    await entered.close()

    engine.stop_error = DockerEngineError("stop response lost", dispatched=True)
    destroyer = _environment(tmp_path, engine, state)
    with pytest.raises(EnvironmentProviderError) as captured:
        await destroyer.destroy()

    assert captured.value.code == "provider_unknown_outcome"
    assert destroyer.dump_state() == state
    await destroyer.close()


async def test_reconcile_recovers_create_without_state_or_start(tmp_path, monkeypatch):
    _skip_eip(monkeypatch)
    engine = _FakeDockerEngine()
    first = _environment(tmp_path, engine)
    await first.prepare()
    state = first.dump_state()
    await first.close()
    recovered = _environment(tmp_path, engine)
    before = len(engine.create_specs)
    assert await recovered.reconcile() == "running"
    assert recovered.dump_state() == state
    assert len(engine.create_specs) == before
    await recovered.close()


async def test_external_registration_separates_logical_and_native_identity(tmp_path, monkeypatch):
    _skip_eip(monkeypatch)
    engine = _FakeDockerEngine()
    original = _environment(tmp_path, engine)
    await original.prepare()
    state = original.dump_state()
    await original.close()
    provider = DockerEnvironmentProvider()
    configuration = provider.validate_configuration(schema_version="1", value={})
    external = provider.create_environment(
        configuration=configuration,
        environment_id="env-registered",
        state=state,
        runtime=DockerProviderRuntime(
            engine=engine,
            bootstrap_store=DirectoryDockerBootstrapStore((tmp_path / "bootstrap").resolve()),
            managed=False,
        ),
    )
    await external.prepare()
    assert external.environment_id == "env-registered"
    assert external.dump_state() == state
    assert len(engine.create_specs) == 1
    assert (
        provider.target_identity(configuration=configuration, state=state)
        == DockerProviderStateData.model_validate(state.state).container_id
    )
    await external.close()
