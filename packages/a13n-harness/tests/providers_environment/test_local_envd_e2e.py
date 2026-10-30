"""Real Host-owned stdio Device and independent Environment Session adapters."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from a13n_envd_client import EIPMethodError
from a13n_envd_client.eip.v1 import DirectoryListParams
from a13n_harness.environment.commands import ArgvCommand, CommandRequest
from a13n_harness.environment.errors import EnvironmentProviderError
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.retention import EnvironmentOutputPolicy
from a13n_harness.providers.environment.local_envd.configuration import (
    LocalEnvdEnvironmentConfiguration,
    LocalEnvdLaunchConfiguration,
)
from a13n_harness.providers.environment.local_envd.provider import LOCAL_ENVD
from a13n_harness.providers.environment.local_envd.runtime import (
    LocalEnvdProviderRuntime,
    TemporaryLocalEnvdRuntimeAllocator,
)

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
        LOCAL_ENVD.execution_connector(
            LocalEnvdEnvironmentConfiguration(working_directory=device_path(root)),
            runtime=runtime,
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
            first, second = await asyncio.gather(first.open(), second.open())
            assert len(list(allocation_parent.iterdir())) == 1
            assert len(device._sessions) == 2
            assert first.descriptor.generation != second.descriptor.generation
            assert first.descriptor.working_directory == device_path(first_root)
            assert second.descriptor.working_directory == device_path(second_root)
            files, processes = second.operations.files, second.operations.processes
            assert files is not None and processes is not None
            await files.write_text(device_path(first_root / "outside-cwd"), "allowed", mode="create")
            assert (first_root / "outside-cwd").read_text() == "allowed"
            # Stage a complete skill through the public file API and replace a nonempty name over EIP.
            candidate, published = second_root / "candidate", second_root / "review"
            await files.mkdir(device_path(candidate))
            await files.mkdir(device_path(published))
            await files.write_text(device_path(candidate / "SKILL.md"), "new", mode="create")
            await files.copy(device_path(candidate / "SKILL.md"), device_path(candidate / "support.txt"))
            await files.write_text(device_path(published / "SKILL.md"), "old", mode="create")
            await files.write_text(device_path(published / "obsolete"), "old", mode="create")
            await files.move(device_path(candidate), device_path(published), replace=True)
            assert (published / "SKILL.md").read_text() == "new"
            assert (published / "support.txt").read_text() == "new"
            assert not (published / "obsolete").exists() and not candidate.exists()
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
            invalid = LOCAL_ENVD.execution_connector(
                LocalEnvdEnvironmentConfiguration(working_directory=device_path(tmp_path / "missing")),
                runtime=runtime,
                environment_id="invalid",
            )
            with pytest.raises(EnvironmentProviderError) as failure:
                await invalid.open()
            assert isinstance(failure.value.__cause__, EIPMethodError)
            assert len(device._sessions) == 1
            assert (await files.read_text(device_path(first_root / "outside-cwd"))).text == "allowed"
        finally:
            await asyncio.gather(first.close(), second.close())
        assert not device._sessions
        again = LOCAL_ENVD.execution_connector(
            LocalEnvdEnvironmentConfiguration(working_directory=device_path(second_root)),
            runtime=runtime,
            environment_id="again",
        )
        try:
            again = await again.open()
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
    adapter = LOCAL_ENVD.execution_connector(
        LocalEnvdEnvironmentConfiguration(), runtime=runtime, environment_id="local"
    )
    adapter = await adapter.open()
    files = adapter.operations.files
    assert files is not None
    await runtime.close()
    with pytest.raises(EnvironmentError) as failure:
        await files.stat(device_path(tmp_path))
    assert failure.value.code == "environment_unavailable"
    await adapter.close()
    assert not list(tmp_path.glob("a13n-local-envd-*"))


@pytest.mark.parametrize("closed", [False, True])
async def test_stdio_recovery_uses_common_session_source(binary, tmp_path, closed):
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState

    runtime = LocalEnvdProviderRuntime(
        executable=binary,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        configuration=LocalEnvdLaunchConfiguration(default_working_directory=tmp_path),
    )
    async with runtime:
        device = await runtime.acquire_device()
        original = await device.open_session(working_directory=device_path(tmp_path))
        recovery = EnvdSessionRecovery(
            EnvdSessionState(
                target=device.descriptor.device_id,
                descriptor=original.descriptor,
                working_directory=device_path(tmp_path),
            )
        )
        if closed:
            await original.close()
        else:
            # Lose only the local wrapper, retaining the Host-owned stdio carrier.
            # EOF would terminate this daemon, tested separately below.
            original._finish()
        execution = await LOCAL_ENVD.execution_connector(
            LocalEnvdEnvironmentConfiguration(working_directory=device_path(tmp_path)),
            runtime=runtime,
            session_recovery=recovery,
        ).open()
        try:
            assert recovery.state is not None
            assert (recovery.state.descriptor.session_id == original.session_id) is not closed
            assert execution.operations.files is not None
            await execution.operations.files.write_text(device_path(tmp_path / "restored"), "once", mode="create")
        finally:
            await execution.close()
        assert recovery.state is None
        assert (tmp_path / "restored").read_text() == "once"
        with pytest.raises(EIPMethodError):
            await device.attach_session(original.descriptor)


async def test_stdio_eof_exits_daemon_and_new_generation_ignores_hint(binary, tmp_path):
    import json

    from a13n_envd_client import EIPDeviceConnection, EIPSessionStateError, EIPTransportClosedError, StdioTransport
    from a13n_harness.environment.attachments import DeviceEIPSessionSource
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"default_working_directory": str(tmp_path)}))
    recovery = EnvdSessionRecovery()
    previous = None
    for index in range(2):
        runtime = tmp_path / f"runtime-{index}"
        runtime.mkdir()
        process = await asyncio.create_subprocess_exec(
            str(binary),
            "--config",
            str(config),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                "A13N_ENVD_DEVICE_ID": "stdio-recovery",
                "A13N_ENVD_RUNTIME_DIR": str(runtime),
                "A13N_ENVD_TRANSPORT": "stdio",
            },
        )
        transport = StdioTransport.from_process(process)
        device = await EIPDeviceConnection.initialize(transport, expected_device_id="stdio-recovery")
        try:
            # Cleanup can observe either the finished Session or the closed Device,
            # depending on when the reader propagates the carrier failure.
            with pytest.raises(
                (EIPSessionStateError, EIPTransportClosedError),
                match=r"Session did not close cleanly|Device connection is closed",
            ):
                async with DeviceEIPSessionSource(device, session_recovery=recovery).open_session(
                    expected_device_id="stdio-recovery", required_methods=frozenset()
                ) as session:
                    assert recovery.state is not None
                    saved = recovery.state
                    if previous is not None:
                        assert session.descriptor.generation != previous.descriptor.generation
                        assert session.session_id != previous.descriptor.session_id
                    # End the actual carrier without sending session.close first.
                    await transport.close()
                    await asyncio.wait_for(process.wait(), 10)
                    assert process.returncode == 0
        finally:
            await device.close()
            if process.returncode is None:
                process.kill()
                await process.wait()
        previous = saved
        recovery.state = saved
