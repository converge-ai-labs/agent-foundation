from __future__ import annotations

import asyncio
import signal as os_signal
import sys
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import Any, cast

import a13n_harness.providers.environment.direct_local.files as files_module
import a13n_harness.providers.environment.direct_local.processes as process_module
import pytest
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness.providers.environment.direct_local.processes import LocalProcessManager
from a13n_harness.providers.environment.direct_local.provider import (
    DIRECT_LOCAL,
    DirectLocalEnvironment,
)
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.models import EnvironmentAction, EnvironmentError, EnvironmentState

pytestmark = pytest.mark.anyio


def _environment(root: Path, *, max_value_bytes: int | None = None) -> DirectLocalEnvironment:
    provider = DIRECT_LOCAL
    value: dict[str, object] = {"root": {"path": str(root)}}
    if max_value_bytes is not None:
        value["max_value_bytes"] = max_value_bytes
    configuration = provider.validate_environment(value)
    environment = provider.construct(
        environment_id="local-test",
        configuration=configuration,
        state=None,
        runtime=None,
    )
    assert isinstance(environment, DirectLocalEnvironment)
    return environment


async def test_direct_local_entry_exposes_provider_owned_file_operations(tmp_path: Path) -> None:
    marker = tmp_path / "host-owned.txt"
    marker.write_text("preserve")
    environment = _environment(tmp_path)

    await environment.enter(mount_id="workspace")
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


@pytest.mark.parametrize("directory", (False, True))
async def test_move_preserves_destination_published_after_precheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    if directory:
        source.mkdir()
        (source / "content").write_bytes(b"candidate")
    else:
        source.write_bytes(b"candidate")
    rename = files_module._rename_no_replace

    def publish_other_first(source: Path, destination: Path) -> None:
        if directory:
            destination.mkdir()
            (destination / "content").write_bytes(b"winner")
        else:
            destination.write_bytes(b"winner")
        rename(source, destination)

    monkeypatch.setattr(files_module, "_rename_no_replace", publish_other_first)
    environment = _environment(tmp_path)
    await environment.enter(mount_id="workspace")
    try:
        await environment.prepare()
        files = environment.operations.files
        assert files is not None
        with pytest.raises(EnvironmentError) as captured:
            await files.move("/source", "/destination")
        assert captured.value.code == "environment_conflict"
        assert (source / "content" if directory else source).read_bytes() == b"candidate"
        assert (destination / "content" if directory else destination).read_bytes() == b"winner"
    finally:
        await environment.close()


@pytest.mark.parametrize("directory", (False, True))
async def test_independent_moves_publish_one_winner_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: bool
) -> None:
    for name in ("first", "second"):
        source = tmp_path / name
        if directory:
            source.mkdir()
            (source / "content").write_text(name)
        else:
            source.write_text(name)
    barrier = Barrier(2)
    rename = files_module._rename_no_replace

    def publish_together(source: Path, destination: Path) -> None:
        barrier.wait(timeout=5)
        rename(source, destination)

    monkeypatch.setattr(files_module, "_rename_no_replace", publish_together)
    first, second = _environment(tmp_path), _environment(tmp_path)
    try:
        for environment in (first, second):
            await environment.enter(mount_id="workspace")
            await environment.prepare()
        first_files, second_files = first.operations.files, second.operations.files
        assert first_files is not None and second_files is not None
        results = await asyncio.gather(
            first_files.move("/first", "/destination"),
            second_files.move("/second", "/destination"),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        assert len(failures) == 1
        assert isinstance(failures[0], EnvironmentError)
        assert failures[0].code == "environment_conflict"
        destination = tmp_path / "destination"
        winner = (destination / "content" if directory else destination).read_text()
        assert winner in {"first", "second"}
        assert not (tmp_path / winner).exists()
        loser = tmp_path / ("second" if winner == "first" else "first")
        assert (loser / "content" if directory else loser).read_text() == loser.name
    finally:
        await first.close()
        await second.close()


async def test_direct_local_backing_survives_reentry_but_not_replacement_or_policy_change(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    async def observe(*, max_value_bytes: int | None = None) -> tuple[str, str | None]:
        environment = _environment(root, max_value_bytes=max_value_bytes)
        assert environment.descriptor.backing_identity is None
        await environment.enter(mount_id="mount")
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
    assert (await observe(max_value_bytes=1024))[1] != identity
    root.rename(tmp_path / "previous-workspace")
    root.mkdir()
    assert (await observe())[1] != identity


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
    provider = DIRECT_LOCAL
    configuration = provider.validate_environment(
        {"root": {"path": str(tmp_path)}},
    )
    assert isinstance(configuration, DirectLocalEnvironmentConfiguration)
    assert configuration.root == DirectLocalRootConfiguration(path=tmp_path)

    with pytest.raises(EnvironmentProviderError) as captured:
        provider.construct(
            environment_id="local-test",
            configuration=configuration,
            state=EnvironmentState(
                provider_key="direct_local",
                state_version="1",
                state={"target": "invalid"},
            ),
            runtime=None,
        )
    assert captured.value.code == "provider_state_invalid"


def test_direct_local_configuration_does_not_require_root_existence(tmp_path: Path) -> None:
    configuration = DirectLocalEnvironmentConfiguration(
        root=DirectLocalRootConfiguration(path=tmp_path / "missing"),
    )
    assert configuration.root.path == tmp_path / "missing"
