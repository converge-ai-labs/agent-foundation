"""Opt-in Host Session ownership tests against embedded Linux VM workers."""

import asyncio
import json
import os
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from a13n_envd_client import EIPDeviceConnection, StdioTransport
from a13n_envd_client.eip.v1 import GrantAccess, VmBackend, VmIsolation, VmMount
from a13n_harness.environment.attachments import DeviceEIPSessionSource
from a13n_harness.environment.envd_policy import EnvdSessionConfiguration, EnvironmentEnvdCredentialResolver
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.session_recovery import EnvdSessionRecovery, EnvdSessionState
from a13n_harness.providers.environment.eip.binding import EIPEnvironmentSession


@pytest.mark.parametrize("backend", [VmBackend.BOXLITE, VmBackend.MICROSANDBOX])
@pytest.mark.timeout(300)
def test_vm_directory_views_restore_exact_session_and_retire(tmp_path: Path, backend: VmBackend):
    if backend.value not in os.environ.get("A13N_ENVD_VM_TEST_BACKENDS", "").split(","):
        pytest.skip("set A13N_ENVD_VM_TEST_BACKENDS and a VM-enabled A13N_ENVD_TEST_BINARY")
    binary = Path(os.environ["A13N_ENVD_TEST_BINARY"]).resolve()
    shared = tmp_path / "shared"
    for name in ("alpha", "beta"):
        (shared / name).mkdir(parents=True)
    runtime = tmp_path / "runtime"
    config = tmp_path / "envd.json"
    config.write_text(json.dumps({"device_id": "vm-host", "full_control": True}))
    selected = EnvdSessionConfiguration(
        working_directory="/mnt/shared",
        isolation=VmIsolation(
            mode="vm",
            backend=backend,
            image="mirror.gcr.io/library/ubuntu:24.04",
            cpus=1,
            memory_mib=1024,
            mounts=(VmMount(source=str(shared), target="/mnt/shared", access=GrantAccess.READ_WRITE),),
        ),
    )

    async def scenario():
        configuration = await selected.resolve(EnvironmentEnvdCredentialResolver())
        checkpoints: list[str] = []

        async def checkpoint(state: EnvdSessionState):
            checkpoints.append(state.model_dump_json())

        owner = EnvdSessionRecovery(checkpoint=checkpoint)
        with (tmp_path / "daemon.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                str(binary),
                "--config",
                str(config),
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": str(tmp_path),
                    "A13N_ENVD_RUNTIME_DIR": str(runtime),
                    "A13N_ENVD_VM_STARTUP_TIMEOUT_MS": "120000",
                },
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
            )
            device = None
            try:
                device = await EIPDeviceConnection.initialize(
                    StdioTransport.from_process(process), expected_device_id="vm-host", request_timeout=120
                )

                def borrow(recovery):
                    return DeviceEIPSessionSource(device, session_recovery=recovery).open_session(
                        expected_device_id="vm-host", required_methods=frozenset(), configuration=configuration
                    )

                async def view(stack, recovery, name):
                    session = await stack.enter_async_context(borrow(recovery))
                    execution = EIPEnvironmentSession(
                        session=session,
                        provider_key="http_envd",
                        environment_id=f"view-{name}",
                        execution_id=f"execution-{name}",
                        working_directory=f"/mnt/shared/{name}",
                    )
                    stack.push_async_callback(execution.close)
                    return session, execution

                async with AsyncExitStack() as first_stack, AsyncExitStack() as second_stack:
                    first, alpha = await view(first_stack, owner, "alpha")
                    second, beta = await view(second_stack, owner, "beta")
                    assert first is second
                    original = first.descriptor
                    assert original.boundary.vm == selected.isolation
                    assert original.working_directory == "/mnt/shared"
                    for name, execution in (("alpha", alpha), ("beta", beta)):
                        files = execution.operations.files
                        assert files is not None
                        await files.write_text("relative.txt", name, mode="create")
                        assert (shared / name / "relative.txt").read_text() == name
                    await first_stack.aclose()
                    assert owner._users == 1
                    assert beta.operations.files is not None
                    await beta.operations.files.write_text("survived.txt", "sibling released", mode="create")
                assert owner._users == 0
                assert not device._sessions
                assert list(runtime.glob("**/vms/session-*")), "detach must preserve the VM"

                restored = EnvdSessionRecovery(
                    state=EnvdSessionState.model_validate_json(checkpoints[-1]), checkpoint=checkpoint
                )
                async with AsyncExitStack() as stack:
                    session, execution = await view(stack, restored.exact(original.session_id), "beta")
                    assert session.session_id == original.session_id
                    assert session.descriptor.owner_epoch > original.owner_epoch
                    assert execution.operations.files is not None
                    await execution.operations.files.write_text("restored.txt", "same VM", mode="create")
                active = EnvdSessionState.model_validate_json(checkpoints[-1])
                await restored.retire(device, target="vm-host")
                assert EnvdSessionState.model_validate_json(checkpoints[-1]).status == "retired"
                assert not list(runtime.glob("**/vms/session-*"))
                assert not list(shared.glob(".eip-stage-vm-*"))
                assert (shared / "beta" / "restored.txt").read_text() == "same VM"
                stale = EnvdSessionRecovery(state=active).exact(original.session_id)
                with pytest.raises(EnvironmentError, match="original Session"):
                    async with borrow(stale):
                        pytest.fail("exact recovery must not create a replacement VM")
                assert not device._sessions
                assert not list(runtime.glob("**/vms/session-*"))
            finally:
                if device is not None:
                    await device.close()
                if process.stdin is not None:
                    process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), 30)
                except TimeoutError:
                    process.kill()
                    await process.wait()
                    raise
                assert process.returncode == 0, (tmp_path / "daemon.log").read_text()[-4000:]

    asyncio.run(scenario())
