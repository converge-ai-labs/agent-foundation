"""Real Host-owned stdio Device and independent Environment Session adapters."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from a13n_envd_client import EIPMethodError
from a13n_envd_client.eip.v1 import DirectoryListParams
from a13n_harness.providers.environment.commands import ArgvCommand, CommandRequest
from a13n_harness.providers.environment.errors import EnvironmentProviderError
from a13n_harness.providers.environment.local_envd.configuration import (
    LocalEnvdEnvironmentConfiguration,
    LocalEnvdLaunchConfiguration,
)
from a13n_harness.providers.environment.local_envd.provider import LocalEnvdEnvironment
from a13n_harness.providers.environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
)
from a13n_harness.providers.environment.models import EnvironmentError
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy

pytestmark = pytest.mark.anyio


def device_path(path):
    value = path.as_posix()
    if os.name == "nt":
        return "/UNC/" + value[2:] if value.startswith("//") else "/" + value
    return value


@pytest.fixture
def binary():
    configured = os.environ.get("A13N_ENVD_TEST_BINARY")
    if configured is None:
        pytest.skip("set A13N_ENVD_TEST_BINARY to run Local Envd integration tests")
    result = Path(configured)
    assert result.is_file()
    return result


async def test_local_shared_device_owns_daemon_not_adapter(binary, tmp_path):
    first_root, second_root = tmp_path / "first", tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    allocation_parent = tmp_path / "allocations"
    allocation_parent.mkdir()
    runtime = LocalEnvdProviderRuntime(
        executable=binary,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=allocation_parent),
        configuration=LocalEnvdLaunchConfiguration(
            default_working_directory=first_root,
            trusted_executable_roots=(Path(sys.executable).resolve().parent,),
        ),
    )
    first, second = [
        LocalEnvdEnvironment(
            LocalEnvdEnvironmentConfiguration(working_directory=device_path(root)),
            runtime,
            environment_id=f"local-{index}",
        )
        for index, root in enumerate((first_root, second_root))
    ]
    async with runtime:
        assert not list(allocation_parent.iterdir())
        descriptor = await runtime.describe()
        device = await runtime.acquire_device()
        assert not device._sessions
        listing = await runtime.list_directories(
            DirectoryListParams(
                expected_device_id=descriptor.device_id,
                expected_generation=descriptor.generation,
                path=device_path(tmp_path),
                limit=10,
            )
        )
        assert {entry.name for entry in listing.entries} >= {"first", "second"}
        assert not device._sessions
        try:
            await asyncio.gather(first.prepare(), second.prepare())
            assert len(list(allocation_parent.iterdir())) == 1
            assert len(device._sessions) == 2
            assert first.descriptor.generation != second.descriptor.generation
            assert first.descriptor.working_directory == device_path(first_root)
            assert second.descriptor.working_directory == device_path(second_root)
            files, processes = second.operations.files, second.operations.processes
            assert files is not None and processes is not None
            await files.write_text(device_path(first_root / "outside-cwd"), "allowed", mode="create")
            assert (first_root / "outside-cwd").read_text() == "allowed"
            started = await processes.start(
                CommandRequest(
                    command=ArgvCommand(
                        executable=Path(sys.executable).resolve().name,
                        arguments=("-c", "import os,time; time.sleep(0.2); print(os.getcwd())"),
                    ),
                    output_policy=EnvironmentOutputPolicy(
                        max_inline_bytes=1024, max_output_bytes=4096, overflow="retain"
                    ),
                )
            )
            await first.close()
            assert len(device._sessions) == 1
            result = await processes.wait(started.process.handle, condition="tree_cleaned", timeout_seconds=5)
            assert result.status.exit_code == 0
            assert (await runtime.describe()).generation == descriptor.generation
            await processes.release(started.process.handle)
            invalid = LocalEnvdEnvironment(
                LocalEnvdEnvironmentConfiguration(working_directory=device_path(tmp_path / "missing")),
                runtime,
                environment_id="invalid",
            )
            with pytest.raises(EnvironmentProviderError) as failure:
                await invalid.prepare()
            assert isinstance(failure.value.__cause__, EIPMethodError)
            await invalid.close()
            assert len(device._sessions) == 1
            assert (await files.read_text(device_path(first_root / "outside-cwd"))).text == "allowed"
        finally:
            await asyncio.gather(first.close(), second.close())
        assert not device._sessions
        again = LocalEnvdEnvironment(
            LocalEnvdEnvironmentConfiguration(working_directory=device_path(second_root)),
            runtime,
            environment_id="again",
        )
        try:
            await again.prepare()
            assert again.descriptor.backing_identity == second.descriptor.backing_identity
            assert again.descriptor.generation != second.descriptor.generation
            assert (await runtime.describe()).generation == descriptor.generation
        finally:
            await again.close()
    assert not list(allocation_parent.iterdir())
    assert (first_root / "outside-cwd").read_text() == "allowed"


async def test_host_shutdown_closes_remaining_adapter_sessions(binary, tmp_path):
    runtime = LocalEnvdProviderRuntime(
        executable=binary,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        configuration=LocalEnvdLaunchConfiguration(default_working_directory=tmp_path),
    )
    adapter = LocalEnvdEnvironment(LocalEnvdEnvironmentConfiguration(), runtime, environment_id="local")
    await adapter.prepare()
    files = adapter.operations.files
    assert files is not None
    await runtime.close()
    with pytest.raises(EnvironmentError) as failure:
        await files.stat(device_path(tmp_path))
    assert failure.value.code == "environment_unavailable"
    await adapter.close()
    assert not list(tmp_path.glob("a13n-local-envd-*"))
