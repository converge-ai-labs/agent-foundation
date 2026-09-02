"""Runnable Host-side examples for the built-in Environment Providers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from a13n_environment_provider import (
    DirectLocalEnvironment,
    DirectoryDockerBootstrapStore,
    DockerEnvironment,
    DockerProviderRuntime,
    DockerSDKEngine,
    Environment,
    EnvironmentOperationFamily,
    EnvironmentProviderSpec,
    EnvironmentState,
    LocalEnvdEnvironment,
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
    build_environment_provider_catalog,
    resolve_agent_envd_executable,
)

DEFAULT_EXAMPLE_DOCKER_IMAGE = "agent-foundation-sandbox:local"
_MESSAGE_PATH = "/provider-example.txt"
_FILE_OPERATIONS: frozenset[EnvironmentOperationFamily] = frozenset({"files"})


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
    """Run one Direct Local adapter against a Host-owned workspace."""

    root = _prepare_workspace(workspace)
    spec = EnvironmentProviderSpec(
        provider_key="a13n.direct-local",
        schema_version="1",
        configuration={
            "environment_id": "direct-local-example",
            "root": {"path": str(root)},
        },
    )
    provider = build_environment_provider_catalog(builtin_keys=(spec.provider_key,)).require(spec.provider_key)
    configuration = provider.validate_configuration(
        schema_version=spec.schema_version,
        value=spec.configuration,
    )
    environment = provider.create_environment(configuration=configuration, state=None)
    if not isinstance(environment, DirectLocalEnvironment):
        raise TypeError("Direct Local Provider returned an unexpected Environment")

    text = await _run_and_close(
        environment,
        lambda: _write_and_read(environment, run_id="run-direct-local"),
    )
    state = environment.dump_state()

    return StatelessExampleResult(
        provider_key=environment.provider_key,
        environment_id=environment.environment_id,
        text=text,
        workspace=root,
        workspace_preserved=root.is_dir(),
        state=state,
    )


async def run_local_envd(
    workspace: Path,
    *,
    executable: Path | None = None,
) -> StatelessExampleResult:
    """Run one private Local Envd generation over a Host-owned workspace."""

    root = _prepare_workspace(workspace)
    spec = EnvironmentProviderSpec(
        provider_key="a13n.local-envd",
        schema_version="1",
        configuration={
            "environment_id": "local-envd-example",
            "workspace": {"path": str(root)},
            "execution_network": "deny",
        },
    )
    provider = build_environment_provider_catalog(builtin_keys=(spec.provider_key,)).require(spec.provider_key)
    configuration = provider.validate_configuration(
        schema_version=spec.schema_version,
        value=spec.configuration,
    )
    runtime = LocalEnvdProviderRuntime(
        executable=resolve_agent_envd_executable(executable),
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(),
    )
    environment = provider.create_environment(
        configuration=configuration,
        state=None,
        runtime=runtime,
    )
    if not isinstance(environment, LocalEnvdEnvironment):
        raise TypeError("Local Envd Provider returned an unexpected Environment")

    text = await _run_and_close(
        environment,
        lambda: _write_and_read(environment, run_id="run-local-envd"),
    )
    state = environment.dump_state()

    return StatelessExampleResult(
        provider_key=environment.provider_key,
        environment_id=environment.environment_id,
        text=text,
        workspace=root,
        workspace_preserved=root.is_dir(),
        state=state,
    )


async def run_docker(
    bootstrap_root: Path,
    *,
    image: str = DEFAULT_EXAMPLE_DOCKER_IMAGE,
) -> DockerExampleResult:
    """Create, re-enter, and explicitly destroy one Docker Environment."""

    spec = EnvironmentProviderSpec(
        provider_key="a13n.docker",
        schema_version="1",
        configuration={
            "environment_id": "docker-example",
            "image": image,
        },
    )
    provider = build_environment_provider_catalog(builtin_keys=(spec.provider_key,)).require(spec.provider_key)
    configuration = provider.validate_configuration(
        schema_version=spec.schema_version,
        value=spec.configuration,
    )
    runtime = DockerProviderRuntime(
        engine=DockerSDKEngine.from_env(),
        bootstrap_store=DirectoryDockerBootstrapStore(bootstrap_root.expanduser().resolve()),
    )

    current_state: EnvironmentState | None = None
    first_text = ""
    reentered_text = ""
    state_version = ""

    async def use_target() -> None:
        nonlocal current_state, first_text, reentered_text, state_version
        first = provider.create_environment(
            configuration=configuration,
            state=None,
            runtime=runtime,
        )
        if not isinstance(first, DockerEnvironment):
            raise TypeError("Docker Provider returned an unexpected Environment")
        try:
            first_text = await _run_and_close(
                first,
                lambda: _write_and_read(first, run_id="run-docker-create"),
            )
        finally:
            current_state = first.dump_state()

        if current_state is None:
            raise RuntimeError("Docker Provider did not publish re-entry state")
        state_version = current_state.state_version

        reentered = provider.create_environment(
            configuration=configuration,
            state=current_state,
            runtime=runtime,
        )
        if not isinstance(reentered, DockerEnvironment):
            raise TypeError("Docker Provider returned an unexpected re-entry Environment")
        try:
            reentered_text = await _run_and_close(
                reentered,
                lambda: _read_existing(reentered, run_id="run-docker-reenter"),
            )
        finally:
            current_state = reentered.dump_state()

    async def destroy_target() -> None:
        nonlocal current_state
        if current_state is None:
            return
        cleanup = provider.create_environment(
            configuration=configuration,
            state=current_state,
            runtime=runtime,
        )
        if not isinstance(cleanup, DockerEnvironment):
            raise TypeError("Docker Provider returned an unexpected cleanup Environment")
        try:
            await _run_and_close(cleanup, cleanup.destroy)
        finally:
            current_state = cleanup.dump_state()

    await _run_with_cleanup(
        use_target,
        destroy_target,
        cleanup_label="Docker destruction",
        group_message="Docker use and destruction failed",
    )

    return DockerExampleResult(
        provider_key=spec.provider_key,
        environment_id="docker-example",
        first_text=first_text,
        reentered_text=reentered_text,
        state_version=state_version,
        destroyed=current_state is None,
    )


async def _run_and_close[T](
    environment: Environment,
    operation: Callable[[], Awaitable[T]],
) -> T:
    return await _run_with_cleanup(
        operation,
        environment.close,
        cleanup_label="Environment close",
        group_message="Environment use and close failed",
    )


async def _run_with_cleanup[T](
    operation: Callable[[], Awaitable[T]],
    cleanup: Callable[[], Awaitable[None]],
    *,
    cleanup_label: str,
    group_message: str,
) -> T:
    use_error: BaseException | None = None
    try:
        return await operation()
    except BaseException as error:
        use_error = error
        raise
    finally:
        try:
            await cleanup()
        except BaseException as cleanup_error:
            if use_error is None:
                raise
            if isinstance(use_error, asyncio.CancelledError):
                use_error.add_note(f"{cleanup_label} also failed: {cleanup_error!r}")
            elif isinstance(cleanup_error, asyncio.CancelledError):
                cleanup_error.add_note(f"Primary operation also failed: {use_error!r}")
                raise cleanup_error from None
            else:
                raise BaseExceptionGroup(
                    group_message,
                    [use_error, cleanup_error],
                ) from None


async def _write_and_read(environment: Environment, *, run_id: str) -> str:
    await _enter(environment, run_id=run_id)
    files = environment.operations.files
    if files is None:
        raise RuntimeError("The entered Environment does not expose file operations")
    await files.write_text(
        _MESSAGE_PATH,
        f"hello from {environment.provider_key}\n",
        mode="upsert",
    )
    return (await files.read_text(_MESSAGE_PATH)).text


async def _read_existing(environment: Environment, *, run_id: str) -> str:
    await _enter(environment, run_id=run_id)
    files = environment.operations.files
    if files is None:
        raise RuntimeError("The entered Environment does not expose file operations")
    return (await files.read_text(_MESSAGE_PATH)).text


async def _enter(environment: Environment, *, run_id: str) -> None:
    await environment.enter(
        thread_id="thread-provider-example",
        run_id=run_id,
        agent_instance_id="agent-provider-example",
        mount_id="workspace",
        host_refs={"example": "environment-provider"},
    )
    await environment.ensure_ready(_FILE_OPERATIONS)


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
