from __future__ import annotations

from pathlib import Path

import pytest
from a13n_environment_provider import (
    DirectLocalEnvironment,
    DirectLocalEnvironmentProvider,
    DirectLocalProviderConfiguration,
    DirectLocalProviderRuntime,
    DirectLocalRootConfiguration,
    EnvironmentAction,
    EnvironmentProviderError,
    EnvironmentState,
)

pytestmark = pytest.mark.anyio


def _environment(root: Path, *, read_only: bool = False) -> DirectLocalEnvironment:
    provider = DirectLocalEnvironmentProvider()
    configuration = provider.validate_configuration(
        schema_version="1",
        value={
            "environment_id": "local-test",
            "root": {"path": str(root), "read_only": read_only},
        },
    )
    environment = provider.create_environment(
        configuration=configuration,
        state=None,
        runtime=DirectLocalProviderRuntime(),
    )
    assert isinstance(environment, DirectLocalEnvironment)
    return environment


async def test_direct_local_entry_exposes_provider_owned_file_operations(tmp_path: Path) -> None:
    marker = tmp_path / "host-owned.txt"
    marker.write_text("preserve")
    environment = _environment(tmp_path)

    await environment.enter(
        thread_id="thread-1",
        run_id="run-1",
        agent_instance_id="agent-1",
        mount_id="workspace",
        host_refs={"session_id": "session-1"},
    )
    await environment.operations.files.write_text("/created.txt", "created", mode="create")  # type: ignore[union-attr]

    assert environment.environment_id == "local-test"
    assert environment.availability.status == "available"
    assert EnvironmentAction.FILE_WRITE_TEXT in environment.descriptor.permissions.operations
    assert (tmp_path / "created.txt").read_text() == "created"
    assert environment.dump_state() is None

    await environment.close()
    assert marker.read_text() == "preserve"
    assert (tmp_path / "created.txt").read_text() == "created"


async def test_direct_local_read_only_configuration_denies_writes(tmp_path: Path) -> None:
    environment = _environment(tmp_path, read_only=True)
    await environment.enter(
        thread_id="thread-1",
        run_id="run-1",
        agent_instance_id="agent-1",
        mount_id="workspace",
    )

    assert EnvironmentAction.FILE_WRITE_TEXT not in environment.descriptor.permissions.operations
    with pytest.raises(Exception) as captured:
        await environment.operations.files.write_text("/denied.txt", "denied", mode="create")  # type: ignore[union-attr]
    assert getattr(captured.value, "code", None) == "environment_denied"
    await environment.close()


async def test_direct_local_destroy_is_non_destructive(tmp_path: Path) -> None:
    marker = tmp_path / "host-owned.txt"
    marker.write_text("preserve")
    environment = _environment(tmp_path)

    await environment.destroy()
    await environment.close()

    assert marker.read_text() == "preserve"
    assert environment.dump_state() is None


def test_direct_local_provider_is_inert_and_rejects_state(tmp_path: Path) -> None:
    provider = DirectLocalEnvironmentProvider()
    configuration = provider.validate_configuration(
        schema_version="1",
        value={"environment_id": "local-test", "root": {"path": str(tmp_path)}},
    )
    assert isinstance(configuration, DirectLocalProviderConfiguration)
    assert configuration.root == DirectLocalRootConfiguration(path=tmp_path)

    with pytest.raises(EnvironmentProviderError) as captured:
        provider.create_environment(
            configuration=configuration,
            state=EnvironmentState(
                provider_key="a13n.direct-local",
                state_version="1",
                state={"target": "invalid"},
            ),
        )
    assert captured.value.code == "provider_state_invalid"


def test_direct_local_configuration_does_not_require_root_existence(tmp_path: Path) -> None:
    configuration = DirectLocalProviderConfiguration(
        environment_id="local-test",
        root=DirectLocalRootConfiguration(path=tmp_path / "missing"),
    )
    assert configuration.root.path == tmp_path / "missing"
