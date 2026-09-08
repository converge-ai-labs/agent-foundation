from __future__ import annotations

import signal as os_signal
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import a13n_environment.direct_local.processes as process_module
import pytest
from a13n_environment import (
    DirectLocalEnvironment,
    DirectLocalEnvironmentProvider,
    DirectLocalProviderConfiguration,
    DirectLocalProviderRuntime,
    DirectLocalRootConfiguration,
    EnvironmentAction,
    EnvironmentProviderError,
    EnvironmentState,
)
from a13n_environment.direct_local.processes import LocalProcessManager

pytestmark = pytest.mark.anyio


def _environment(root: Path, *, read_only: bool = False) -> DirectLocalEnvironment:
    provider = DirectLocalEnvironmentProvider()
    configuration = provider.validate_configuration(
        schema_version="1",
        value={
            "root": {"path": str(root), "read_only": read_only},
        },
    )
    environment = provider.create_environment(
        environment_id="local-test",
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
    await environment.prepare()
    await environment.operations.files.write_text("/created.txt", "created", mode="create")  # type: ignore[union-attr]

    assert environment.environment_id == "local-test"
    assert environment.availability.status == "available"
    assert EnvironmentAction.FILE_WRITE_TEXT in environment.descriptor.permissions.operations
    assert (tmp_path / "created.txt").read_text() == "created"
    assert environment.dump_state() is None

    await environment.close()
    assert marker.read_text() == "preserve"
    assert (tmp_path / "created.txt").read_text() == "created"


async def test_direct_local_backing_survives_reentry_but_not_replacement_or_policy_change(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    async def observe(*, read_only: bool = False) -> tuple[str, str | None]:
        environment = _environment(root, read_only=read_only)
        assert environment.descriptor.backing_identity is None
        await environment.enter(thread_id="thread", run_id="run", agent_instance_id="agent", mount_id="mount")
        assert environment.descriptor.backing_identity is None
        try:
            await environment.prepare()
            return environment.descriptor.generation, environment.descriptor.backing_identity
        finally:
            await environment.close()

    generation, identity = await observe()
    assert identity is not None
    (root / "normal-write").write_text("ordinary content changes preserve backing")
    next_generation, next_identity = await observe()
    assert next_generation != generation
    assert next_identity == identity
    assert (await observe(read_only=True))[1] != identity
    root.rename(tmp_path / "previous-workspace")
    root.mkdir()
    assert (await observe())[1] != identity


async def test_direct_local_read_only_configuration_denies_writes(tmp_path: Path) -> None:
    environment = _environment(tmp_path, read_only=True)
    await environment.enter(
        thread_id="thread-1",
        run_id="run-1",
        agent_instance_id="agent-1",
        mount_id="workspace",
    )
    await environment.prepare()

    assert EnvironmentAction.FILE_WRITE_TEXT not in environment.descriptor.permissions.operations
    with pytest.raises(Exception) as captured:
        await environment.operations.files.write_text("/denied.txt", "denied", mode="create")  # type: ignore[union-attr]
    assert getattr(captured.value, "code", None) == "environment_denied"
    await environment.close()


@pytest.mark.skipif(sys.platform == "win32", reason="Direct Local process groups require POSIX")
@pytest.mark.parametrize("denied_signal_name", ("SIGTERM", "SIGKILL"))
async def test_process_group_cleanup_treats_permission_denial_as_terminal(
    monkeypatch: pytest.MonkeyPatch,
    denied_signal_name: str,
) -> None:
    denied_signal = cast(int, getattr(os_signal, denied_signal_name))
    manager = cast(Any, object.__new__(LocalProcessManager))
    manager._policy = SimpleNamespace(terminate_grace_seconds=0)
    signals: list[int] = []

    def killpg(process_group: int, sent_signal: int) -> None:
        assert process_group == 123
        signals.append(sent_signal)
        if sent_signal == denied_signal:
            raise PermissionError

    monkeypatch.setattr(process_module.os, "killpg", killpg)

    await manager._cleanup_group(123)

    assert signals == (
        [os_signal.SIGTERM] if denied_signal == os_signal.SIGTERM else [os_signal.SIGTERM, os_signal.SIGKILL]
    )


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
        value={"root": {"path": str(tmp_path)}},
    )
    assert isinstance(configuration, DirectLocalProviderConfiguration)
    assert configuration.root == DirectLocalRootConfiguration(path=tmp_path)

    with pytest.raises(EnvironmentProviderError) as captured:
        provider.create_environment(
            environment_id="local-test",
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
        root=DirectLocalRootConfiguration(path=tmp_path / "missing"),
    )
    assert configuration.root.path == tmp_path / "missing"
