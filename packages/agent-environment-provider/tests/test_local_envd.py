from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from a13n_environment_provider import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentOperations,
    EnvironmentPermissionSet,
    EnvironmentProviderError,
    EnvironmentState,
    LocalEnvdEnvironment,
    LocalEnvdEnvironmentProvider,
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
)
from a13n_environment_provider.local_envd import provider as provider_module

pytestmark = pytest.mark.anyio


class _Carrier:
    def __init__(self) -> None:
        self.closed = False

    def lease(self, **_kwargs: Any) -> object:
        return object()

    async def close(self) -> None:
        self.closed = True


class _BoundEIP:
    def __init__(self) -> None:
        self.descriptor = EnvironmentDescriptor(
            generation="generation-test",
            operation_families=frozenset(),
            permissions=EnvironmentPermissionSet(),
        )
        self.operations = EnvironmentOperations()
        self.availability = EnvironmentAvailability(status="available")
        self.ready_calls: list[frozenset[str]] = []

    async def ensure_ready(self, operations: frozenset[str]) -> None:
        self.ready_calls.append(operations)


def _environment(tmp_path: Path) -> LocalEnvdEnvironment:
    provider = LocalEnvdEnvironmentProvider()
    configuration = provider.validate_configuration(
        schema_version="1",
        value={
            "workspace": {"path": str(tmp_path)},
        },
    )
    environment = provider.create_environment(
        environment_id="local-envd-test",
        configuration=configuration,
        state=None,
        runtime=LocalEnvdProviderRuntime(
            executable=tmp_path / "agent-envd",
            allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        ),
    )
    assert isinstance(environment, LocalEnvdEnvironment)
    return environment


def _patch_entry(
    monkeypatch: pytest.MonkeyPatch,
    environment: LocalEnvdEnvironment,
) -> tuple[_Carrier, _BoundEIP, list[str]]:
    carrier = _Carrier()
    bound = _BoundEIP()
    events: list[str] = []

    async def validate_runtime(*_args: Any, **_kwargs: Any) -> None:
        events.append("validate")

    async def launch() -> None:
        events.append("launch")
        environment._carrier = carrier  # type: ignore[assignment]

    @asynccontextmanager
    async def open_environment(**_kwargs: Any):
        events.append("eip-enter")
        try:
            yield bound
        finally:
            events.append("eip-exit")

    monkeypatch.setattr(provider_module, "_validate_runtime", validate_runtime)
    monkeypatch.setattr(environment, "_launch_private_generation", launch)
    monkeypatch.setattr(provider_module, "open_eip_environment", open_environment)
    return carrier, bound, events


async def test_local_envd_uses_fresh_private_generation_and_non_destructive_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _environment(tmp_path)
    carrier, bound, events = _patch_entry(monkeypatch, environment)

    await environment.enter(
        thread_id="thread-1",
        run_id="run-1",
        agent_instance_id="agent-1",
        mount_id="workspace",
        host_refs={"session_id": "session-1"},
    )
    await environment.prepare()

    assert environment.is_entered
    assert environment.dump_state() is None
    assert bound.ready_calls == [frozenset()]
    assert events == ["validate", "launch", "eip-enter"]

    await environment.close()
    assert events == ["validate", "launch", "eip-enter", "eip-exit"]
    assert carrier.closed
    assert tmp_path.is_dir()


async def test_local_envd_destroy_does_not_delete_host_workspace(tmp_path: Path) -> None:
    marker = tmp_path / "host-owned.txt"
    marker.write_text("preserve")
    environment = _environment(tmp_path)

    await environment.destroy()
    await environment.close()

    assert marker.read_text() == "preserve"
    assert environment.dump_state() is None


def test_local_envd_provider_rejects_persisted_pid_or_target_state(tmp_path: Path) -> None:
    provider = LocalEnvdEnvironmentProvider()
    configuration = provider.validate_configuration(
        schema_version="1",
        value={"workspace": {"path": str(tmp_path)}},
    )

    with pytest.raises(EnvironmentProviderError) as captured:
        provider.create_environment(
            environment_id="local-envd-test",
            configuration=configuration,
            state=EnvironmentState(
                provider_key="a13n.local-envd",
                state_version="1",
                state={"pid": 1234},
            ),
            runtime=LocalEnvdProviderRuntime(
                executable=tmp_path / "agent-envd",
                allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
            ),
        )
    assert captured.value.code == "provider_state_invalid"


def test_local_envd_provider_constructs_distinct_inert_adapters(tmp_path: Path) -> None:
    first = _environment(tmp_path)
    second = _environment(tmp_path)

    assert first is not second
    assert first.dump_state() is None
    assert second.dump_state() is None
