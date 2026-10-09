"""Standalone management and execution examples; Harness is not required."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from a13n_environment import EnvironmentExecution
from a13n_environment.direct_local.provider import DIRECT_LOCAL
from a13n_environment.docker.provider import DOCKER
from a13n_environment.errors import observed_environment_state
from a13n_environment.local_envd.provider import LOCAL_ENVD
from a13n_environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
    resolve_a13n_envd_executable,
)
from a13n_environment.models import EnvironmentState

DEFAULT_EXAMPLE_DOCKER_IMAGE = "a13n-sandbox:local"
_MESSAGE_PATH = "/provider-example.txt"


@dataclass(frozen=True, slots=True)
class StatelessExampleResult:
    """Observable result from a Direct Local or Local Envd example."""

    provider_key: str
    environment_id: str
    text: str
    workspace: Path
    workspace_preserved: bool
    state: EnvironmentState | None


@dataclass(frozen=True, slots=True)
class DockerExampleResult:
    """Observable result from the stateful Docker lifecycle example."""

    provider_key: str
    environment_id: str
    first_text: str
    reentered_text: str
    state_version: str
    destroyed: bool


async def run_direct_local(workspace: Path) -> StatelessExampleResult:
    root = _prepare_workspace(workspace)
    connector = DIRECT_LOCAL.execution_connector({"root": {"path": str(root)}}, environment_id="direct-local-example")
    async with await connector.open() as execution:
        text = await _write_and_read(execution)
    return StatelessExampleResult(
        connector.provider_key, connector.environment_id, text, root, root.is_dir(), connector.state
    )


async def run_local_envd(workspace: Path, *, executable: Path | None = None) -> StatelessExampleResult:
    root = _prepare_workspace(workspace)
    async with LocalEnvdProviderRuntime(
        executable=resolve_a13n_envd_executable(executable),
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(),
    ) as runtime:
        connector = LOCAL_ENVD.execution_connector(
            {"working_directory": _device_path(root)},
            environment_id="local-envd-example",
            runtime=runtime,
        )
        async with await connector.open() as execution:
            text = await _write_and_read(execution, path=_device_path(root / "provider-example.txt"))
    return StatelessExampleResult(
        connector.provider_key, connector.environment_id, text, root, root.is_dir(), connector.state
    )


async def run_docker(*, image: str = DEFAULT_EXAMPLE_DOCKER_IMAGE) -> DockerExampleResult:
    recipe = DOCKER.validate_environment({"image": image})
    async with await DOCKER.open_provider() as provider:
        state = None
        first_text = reentered_text = state_version = ""

        async def use():
            nonlocal state, first_text, reentered_text, state_version
            try:
                state = await provider.create(recipe, environment_id="docker-example", operation_id="op-create")
            except BaseException as error:
                state = observed_environment_state(error, state)
                raise
            assert state is not None
            state_version = state.state_version
            connector = provider.execution_connector(recipe, environment_id="docker-example", state=state)
            async with await connector.open() as first:
                first_text = await _write_and_read(first)
            async with await connector.open() as second:
                assert second.operations.files is not None
                reentered_text = (await second.operations.files.read_text(_MESSAGE_PATH)).text

        async def destroy():
            if state is not None:
                await provider.destroy(recipe, environment_id="docker-example", state=state, operation_id="op-delete")

        await _run_with_cleanup(use, destroy)
    return DockerExampleResult("docker", "docker-example", first_text, reentered_text, state_version, True)


async def _run_with_cleanup[T](operation: Callable[[], Awaitable[T]], cleanup: Callable[[], Awaitable[None]]) -> T:
    primary: BaseException | None = None
    try:
        return await operation()
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            await cleanup()
        except BaseException as error:
            if primary is None:
                raise
            if isinstance(primary, asyncio.CancelledError):
                primary.add_note(f"Target cleanup also failed: {error!r}")
            else:
                raise BaseExceptionGroup("Target use and cleanup failed", [primary, error]) from None


def _device_path(path: Path) -> str:
    value = path.as_posix()
    return f"/{value}" if os.name == "nt" else value


async def _write_and_read(execution: EnvironmentExecution, *, path: str = _MESSAGE_PATH) -> str:
    files = execution.operations.files
    assert files is not None
    await files.write_text(path, f"hello from {execution.provider_key}\n", mode="upsert")
    return (await files.read_text(path)).text


def _prepare_workspace(workspace: Path) -> Path:
    root = workspace.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


__all__ = [
    "DEFAULT_EXAMPLE_DOCKER_IMAGE",
    "DockerExampleResult",
    "StatelessExampleResult",
    "run_direct_local",
    "run_docker",
    "run_local_envd",
]
