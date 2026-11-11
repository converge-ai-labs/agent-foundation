"""Real Host-owned stdio Device and independent Environment Session adapters."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from a13n_envd_client.eip.v1 import DirectoryListParams, SessionConfiguration
from a13n_envd_client.errors import EIPMethodError
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
                LocalEnvdEnvironmentConfiguration(session={"working_directory": device_path(tmp_path / "missing")}),
                runtime=runtime,
                environment_id="invalid",
            )
            with pytest.raises(EnvironmentProviderError) as failure:
                await invalid.open()
            assert isinstance(failure.value.__cause__, EIPMethodError)
            assert failure.value.__cause__.error.code == -32011
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
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState, configuration_digest

    runtime = LocalEnvdProviderRuntime(
        executable=binary,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        configuration=LocalEnvdLaunchConfiguration(default_working_directory=tmp_path),
    )
    async with runtime:
        device = await runtime.acquire_device()
        original = await device.open_session(configuration=SessionConfiguration())
        recovery = EnvdSessionRecovery(
            EnvdSessionState(
                target=device.descriptor.device_id,
                descriptor=original.descriptor,
                configuration_digest=configuration_digest(SessionConfiguration()),
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
        assert recovery.state is not None
        assert (tmp_path / "restored").read_text() == "once"
        resumed = await device.attach_session(recovery.state.descriptor)
        assert (await resumed.readiness()).ready
        await resumed.close()


async def test_stdio_eof_exits_daemon_and_new_generation_ignores_hint(binary, tmp_path):
    import json
    from contextlib import suppress

    from a13n_envd_client import EIPDeviceConnection, EIPTransportClosedError, StdioTransport
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
            with suppress(EIPTransportClosedError):
                async with DeviceEIPSessionSource(device, session_recovery=recovery).open_session(
                    expected_device_id="stdio-recovery", required_methods=frozenset()
                ) as session:
                    assert recovery.state is not None
                    saved = recovery.state
                    if previous is not None:
                        assert session.descriptor.generation != previous.descriptor.generation
                        assert session.session_id != previous.descriptor.session_id
                    await transport.close()
                    await asyncio.wait_for(process.wait(), 10)
                    assert process.returncode == 0
            assert recovery.state == saved
        finally:
            await device.close()
            if process.returncode is None:
                process.kill()
                await process.wait()
        previous = saved
        recovery.state = saved


async def test_directory_views_share_session_but_own_cwd_and_processes(binary, tmp_path):
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery

    roots = (tmp_path / "alpha", tmp_path / "beta")
    for root in roots:
        root.mkdir()
    recovery = EnvdSessionRecovery()
    async with LocalEnvdProviderRuntime(
        executable=binary,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        configuration=LocalEnvdLaunchConfiguration(
            default_working_directory=tmp_path,
            trusted_executable_roots=(Path(sys.executable).resolve().parent,),
        ),
    ) as runtime:
        device = await runtime.acquire_device()
        first, second = await asyncio.gather(
            *(
                LOCAL_ENVD.execution_connector(
                    LocalEnvdEnvironmentConfiguration(working_directory=device_path(root)),
                    runtime=runtime,
                    environment_id=f"view-{index}",
                    session_recovery=recovery,
                ).open()
                for index, root in enumerate(roots)
            )
        )
        try:
            assert first is not second
            assert first.descriptor.generation == second.descriptor.generation
            assert len(device._sessions) == 1
            assert recovery.state is not None
            original = recovery.state.descriptor
            for execution, root in zip((first, second), roots, strict=True):
                assert execution.operations.files is not None
                await execution.operations.files.write_text("relative.txt", root.name, mode="create")
                assert (root / "relative.txt").read_text() == root.name
            processes = second.operations.processes
            assert processes is not None
            started = await processes.start(
                CommandRequest(
                    command=ArgvCommand(
                        executable=Path(sys.executable).resolve().name,
                        arguments=(
                            "-c",
                            "from pathlib import Path; import time; time.sleep(0.2); Path('cwd.txt').write_text(str(Path.cwd()))",
                        ),
                    ),
                    output_policy=EnvironmentOutputPolicy(
                        max_inline_bytes=1024, max_output_bytes=4096, overflow="retain"
                    ),
                )
            )
            await first.close()
            assert len(device._sessions) == 1
            assert recovery._users == 1
            result = await processes.wait(started.process.handle, condition="tree_cleaned", timeout_seconds=5)
            assert result.status.exit_code == 0
            assert (roots[1] / "cwd.txt").read_text() == str(roots[1])
            assert not (roots[0] / "cwd.txt").exists()
            await processes.release(started.process.handle)
        finally:
            await first.close()
            await second.close()
        assert recovery._users == 0
        assert not device._sessions
        resumed = await LOCAL_ENVD.execution_connector(
            LocalEnvdEnvironmentConfiguration(working_directory=device_path(roots[1])),
            runtime=runtime,
            environment_id="renamed-view",
            session_recovery=recovery,
        ).open()
        try:
            assert recovery.state.descriptor.session_id == original.session_id
            assert recovery.state.descriptor.owner_epoch > original.owner_epoch
        finally:
            await resumed.close()


async def test_session_replacement_requires_retirement_and_never_resurrects(binary, tmp_path):
    from a13n_envd_client import EIPSessionStateError
    from a13n_harness.environment.attachments import DeviceEIPSessionSource
    from a13n_harness.environment.models import EnvironmentError
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery

    checkpoints = []

    async def checkpoint(state):
        checkpoints.append(state)

    recovery = EnvdSessionRecovery(checkpoint=checkpoint)
    async with LocalEnvdProviderRuntime(
        executable=binary,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        configuration=LocalEnvdLaunchConfiguration(default_working_directory=tmp_path),
    ) as runtime:
        device = await runtime.acquire_device()
        target = device.descriptor.device_id

        def borrow(configuration=None):
            return DeviceEIPSessionSource(device, session_recovery=recovery).open_session(
                expected_device_id=target,
                required_methods=frozenset(),
                configuration=configuration,
            )

        async with borrow() as first:
            first_id = first.session_id
            with pytest.raises(EIPSessionStateError, match="busy"):
                await recovery.retire(device, target=target)
        with pytest.raises(EIPSessionStateError, match="retire"):
            async with borrow(SessionConfiguration(commands=False)):
                pytest.fail("configuration change must not open a sibling Session")
        await recovery.retire(device, target=target)
        assert checkpoints[-1].status == "retired"
        assert any(state.status == "retiring" for state in checkpoints)
        async with borrow(SessionConfiguration(commands=False)) as second:
            assert second.session_id != first_id
            second_id = second.session_id
        await recovery.retire(device, target=target)
        async with borrow() as third:
            assert third.session_id not in {first_id, second_id}
            original = recovery.state
        await recovery.retire(device, target=target)
        assert original is not None
        exact = EnvdSessionRecovery(state=original).exact(original.descriptor.session_id)
        with pytest.raises(EnvironmentError, match="original Session"):
            async with DeviceEIPSessionSource(device, session_recovery=exact).open_session(
                expected_device_id=target,
                required_methods=frozenset(),
                configuration=SessionConfiguration(),
            ):
                pytest.fail("retained work must never open a successor")
        assert not device._sessions


async def test_host_configuration_identity_survives_detach_and_restart(binary, tmp_path):
    from a13n_envd_client import EIPSessionStateError
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState

    async with LocalEnvdProviderRuntime(
        executable=binary,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        configuration=LocalEnvdLaunchConfiguration(default_working_directory=tmp_path),
    ) as runtime:
        device = await runtime.acquire_device()
        recovery = EnvdSessionRecovery()

        def borrow(owner, identity):
            return owner.borrow(
                device,
                target=device.descriptor.device_id,
                expected_device_id=device.descriptor.device_id,
                configuration=SessionConfiguration(),
                configuration_identity=identity,
                required_methods=frozenset(),
                expected_boundary=None,
            )

        async with borrow(recovery, "selection-a") as first:
            session_id = first.session_id
            async with borrow(recovery, "selection-a") as shared:
                assert shared is first
            with pytest.raises(EIPSessionStateError, match="incompatible"):
                async with borrow(recovery, "selection-b"):
                    pytest.fail("an active borrower must not change the binding identity")
        assert recovery.state is not None
        restored = EnvdSessionRecovery(state=EnvdSessionState.model_validate_json(recovery.state.model_dump_json()))
        async with borrow(restored, "selection-a") as attached:
            assert attached.session_id == session_id
            assert attached.descriptor.owner_epoch > recovery.state.descriptor.owner_epoch
        with pytest.raises(EIPSessionStateError, match="retire"):
            async with borrow(restored, "selection-b"):
                pytest.fail("a changed selection requires explicit retirement")
        await restored.retire(device, target=device.descriptor.device_id)
        async with borrow(restored, "selection-b") as replacement:
            assert replacement.session_id != session_id


async def test_exact_restore_rejects_a_reloaded_successor_before_taking_ownership(binary, tmp_path):
    from a13n_harness.environment.attachments import DeviceEIPSessionSource
    from a13n_harness.environment.models import EnvironmentError
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery

    current = None
    checkpoints = []

    async def checkpoint(state):
        nonlocal current
        current = state
        checkpoints.append(state)

    owner = EnvdSessionRecovery(checkpoint=checkpoint)
    async with LocalEnvdProviderRuntime(
        executable=binary,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        configuration=LocalEnvdLaunchConfiguration(default_working_directory=tmp_path),
    ) as runtime:
        device = await runtime.acquire_device()
        target = device.descriptor.device_id

        def borrow(recovery):
            return DeviceEIPSessionSource(device, session_recovery=recovery).open_session(
                expected_device_id=target, required_methods=frozenset()
            )

        async with borrow(owner):
            original = current
        assert original is not None
        restored = EnvdSessionRecovery(state=original, checkpoint=checkpoint)
        exact = restored.exact(original.descriptor.session_id)
        await owner.retire(device, target=target)
        async with borrow(owner) as successor:
            successor_id = successor.session_id
        checkpoint_count = len(checkpoints)
        # Hosts reload the durable head under their own lock before acquisition.
        restored.state = current
        with pytest.raises(EnvironmentError, match="original Session"):
            async with borrow(exact):
                pytest.fail("old work must not attach to the new Session")
        assert len(checkpoints) == checkpoint_count
        assert current.descriptor.session_id == successor_id
        assert not device._sessions
        async with borrow(owner) as still_current:
            assert still_current.session_id == successor_id


async def test_local_exact_recovery_skips_startup_secret_resolution(binary, tmp_path, monkeypatch):
    from a13n_envd_client import EIPDeviceConnection
    from a13n_harness.environment.envd_policy import EnvdEgressConfiguration, EnvdSessionConfiguration
    from a13n_harness.environment.models import EnvironmentError
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery

    owner = EnvdSessionRecovery()
    async with LocalEnvdProviderRuntime(
        executable=binary,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        configuration=LocalEnvdLaunchConfiguration(default_working_directory=tmp_path),
    ) as runtime:
        connector = LOCAL_ENVD.execution_connector(
            LocalEnvdEnvironmentConfiguration(), runtime=runtime, environment_id="view", session_recovery=owner
        )
        original = await connector.open()
        await original.close()
        previous = owner.state
        assert previous is not None
        monkeypatch.delenv("REMOVED_SESSION_SECRET", raising=False)
        configuration = LocalEnvdEnvironmentConfiguration(
            session=EnvdSessionConfiguration(
                egress=EnvdEgressConfiguration.model_validate(
                    {
                        "destinations": {"mode": "allowlist"},
                        "secrets": [
                            {
                                "env": "API_KEY",
                                "source": {"kind": "environment", "name": "REMOVED_SESSION_SECRET"},
                                "inject_hosts": ["example.com"],
                            }
                        ],
                    }
                )
            )
        )
        restored = EnvdSessionRecovery(state=previous)
        exact = restored.exact(previous.descriptor.session_id)

        async def forbidden_open(*args, **kwargs):
            pytest.fail("exact recovery must not open a successor")

        monkeypatch.setattr(EIPDeviceConnection, "open_session", forbidden_open)
        recovered = await LOCAL_ENVD.execution_connector(
            configuration, runtime=runtime, environment_id="view", session_recovery=exact
        ).open()
        await recovered.close()
        assert restored.state.descriptor.session_id == previous.descriptor.session_id
        assert restored.state.configuration_digest == previous.configuration_digest
        assert restored.state.descriptor.owner_epoch > previous.descriptor.owner_epoch
        device = await runtime.acquire_device()
        stale = EnvdSessionRecovery(state=restored.state).exact(previous.descriptor.session_id)
        await restored.retire(device, target=previous.target)
        with pytest.raises(EnvironmentError) as lost:
            await LOCAL_ENVD.execution_connector(
                configuration, runtime=runtime, environment_id="view", session_recovery=stale
            ).open()
        assert lost.value.code == "environment_not_found"


async def test_session_replacement_notice_follows_checkpoint_and_shared_acquisition(binary, tmp_path):
    from a13n_harness.environment.attachments import DeviceEIPSessionSource
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, envd_session_notifications

    notices = []
    checkpoints = []

    async def checkpoint(state):
        checkpoints.append(state)

    recovery = EnvdSessionRecovery(checkpoint=checkpoint)

    async def notify(session_id):
        assert not recovery._lock.locked()
        assert recovery.state.descriptor.session_id == checkpoints[-1].descriptor.session_id == session_id
        notices.append(session_id)

    async with LocalEnvdProviderRuntime(
        executable=binary,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        configuration=LocalEnvdLaunchConfiguration(default_working_directory=tmp_path),
    ) as runtime:
        device = await runtime.acquire_device()

        def borrow(owner=recovery):
            return DeviceEIPSessionSource(device, session_recovery=owner).open_session(
                expected_device_id=device.descriptor.device_id, required_methods=frozenset()
            )

        with envd_session_notifications(notify):
            async with borrow() as first:
                original_id = first.session_id
            async with borrow() as attached:
                assert attached.session_id == original_id
                await attached.close()
            assert notices == []

            async def acquire():
                async with borrow() as session:
                    await asyncio.sleep(0)
                    return session.session_id

            successors = await asyncio.gather(*(acquire() for _ in range(3)))
            assert len(set(successors)) == 1 and successors[0] != original_id
            assert notices == [successors[0]]
            with pytest.raises(EnvironmentError, match="original Session"):
                async with borrow(recovery.exact(original_id)):
                    pytest.fail("exact work must not open a successor")
            assert notices == [successors[0]]


async def test_session_replacement_checkpoint_failure_emits_no_notice(binary, tmp_path):
    from a13n_harness.environment.attachments import DeviceEIPSessionSource
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, envd_session_notifications

    original_id = None
    notices = []

    async def checkpoint(state):
        if original_id is not None and state.descriptor.session_id != original_id:
            raise RuntimeError("checkpoint unavailable")

    async def notify(session_id):
        notices.append(session_id)

    recovery = EnvdSessionRecovery(checkpoint=checkpoint)
    async with LocalEnvdProviderRuntime(
        executable=binary,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        configuration=LocalEnvdLaunchConfiguration(default_working_directory=tmp_path),
    ) as runtime:
        device = await runtime.acquire_device()

        def borrow():
            return DeviceEIPSessionSource(device, session_recovery=recovery).open_session(
                expected_device_id=device.descriptor.device_id, required_methods=frozenset()
            )

        with envd_session_notifications(notify):
            async with borrow() as original:
                original_id = original.session_id
                await original.close()
            with pytest.raises(RuntimeError, match="checkpoint unavailable"):
                async with borrow():
                    pytest.fail("uncheckpointed successor must not be exposed")
        assert notices == []
        assert recovery._users == 0 and recovery._session is None
        assert not device._sessions


@pytest.mark.parametrize("cancel", [False, True])
async def test_session_notice_failure_preserves_acquisition_or_cancellation(binary, tmp_path, cancel):
    from a13n_harness.environment.attachments import DeviceEIPSessionSource
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery, envd_session_notifications

    recovery = EnvdSessionRecovery()

    async def notify(session_id):
        assert recovery.state.descriptor.session_id == session_id
        if cancel:
            raise asyncio.CancelledError()
        raise RuntimeError("notice unavailable")

    async with LocalEnvdProviderRuntime(
        executable=binary,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        configuration=LocalEnvdLaunchConfiguration(default_working_directory=tmp_path),
    ) as runtime:
        device = await runtime.acquire_device()

        def borrow():
            return DeviceEIPSessionSource(device, session_recovery=recovery).open_session(
                expected_device_id=device.descriptor.device_id, required_methods=frozenset()
            )

        async with borrow() as original:
            original_id = original.session_id
            await original.close()

        async def acquire():
            with envd_session_notifications(notify):
                async with borrow() as successor:
                    assert successor.session_id != original_id

        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await acquire()
        else:
            await acquire()
        assert recovery._users == 0 and recovery._session is None
        assert not device._sessions


@pytest.mark.parametrize("phase", ["preparation", "execution"])
async def test_non_tool_session_replacement_notifies_only_the_originating_run(binary, tmp_path, phase):
    from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
    from a13n_harness.environment.attachments import DeviceEIPSessionSource
    from a13n_harness.environment.session_recovery import EnvdSessionRecovery
    from a13n_harness.events import HarnessEvent, HarnessExtensionEvent
    from pydantic_ai.messages import UserPromptPart
    from pydantic_ai.models.function import FunctionModel

    recovery = EnvdSessionRecovery()
    calls = []
    async with LocalEnvdProviderRuntime(
        executable=binary,
        allocate_private_runtime=TemporaryLocalEnvdRuntimeAllocator(parent=tmp_path),
        configuration=LocalEnvdLaunchConfiguration(default_working_directory=tmp_path),
    ) as runtime:
        device = await runtime.acquire_device()

        def borrow():
            return DeviceEIPSessionSource(device, session_recovery=recovery).open_session(
                expected_device_id=device.descriptor.device_id, required_methods=frozenset()
            )

        async with borrow() as original:
            original_id = original.session_id
            await original.close()

        async def acquire():
            async with borrow() as successor:
                assert successor.session_id != original_id

        async def input_factory(preparation):
            if phase == "preparation":
                await acquire()
            return "start"

        async def model(messages, info):
            calls.append(messages)
            if phase == "execution" and len(calls) == 1:
                await acquire()
            yield "done"

        executable = HarnessBuilder().build(AgentSpec(), model=FunctionModel(stream_function=model), output_type=str)
        async with executable.stream(input_factory=input_factory, bindings=RunBindings.embedded()) as stream:
            events = [event async for event in stream]
            result = stream.result
        notices = [
            event.event.payload
            for event in events
            if isinstance(event, HarnessEvent)
            and isinstance(event.event, HarnessExtensionEvent)
            and isinstance(event.event.payload, dict)
            and event.event.payload.get("source") == "envd_session"
        ]
        assert len(notices) == 1 and notices[0]["references"] == [recovery.state.descriptor.session_id]
        assert (
            sum(
                "The Envd Session was replaced" in str(part.content)
                for message in result.state.message_history
                for part in message.parts
                if isinstance(part, UserPromptPart)
            )
            == 1
        )
        # A fresh logical Run may reuse the recovery owner, but not an old Run's hint.
        calls.clear()
        async with executable.stream("continue", bindings=RunBindings.embedded()) as next_run:
            following = [event async for event in next_run]
        assert not any(
            isinstance(event, HarnessEvent)
            and isinstance(event.event, HarnessExtensionEvent)
            and isinstance(event.event.payload, dict)
            and event.event.payload.get("source") == "envd_session"
            for event in following
        )
