"""Opt-in Linux KVM tests for the public Python client and embedded VM workers."""

import asyncio
import json
import os
from contextlib import suppress
from pathlib import Path

import pytest
from a13n_envd_client import EIPDeviceConnection, StdioTransport
from a13n_envd_client.eip.v1 import (
    EIPCallContext,
    EIPPath,
    FileReadTextParams,
    GrantAccess,
    SessionConfiguration,
    VmBackend,
    VmIsolation,
    VmMount,
)


@pytest.mark.parametrize("backend", [VmBackend.BOXLITE, VmBackend.MICROSANDBOX])
def test_vm_cancelled_open_reclaims_storage_and_allows_replacement(tmp_path: Path, backend: VmBackend):
    if backend.value not in os.environ.get("A13N_ENVD_VM_TEST_BACKENDS", "").split(","):
        pytest.skip("set A13N_ENVD_VM_TEST_BACKENDS and a VM-enabled A13N_ENVD_TEST_BINARY")
    binary = Path(os.environ["A13N_ENVD_TEST_BINARY"]).resolve()
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "sentinel").write_text("host workspace")
    runtime = tmp_path / "runtime"
    config = tmp_path / "envd.json"
    config.write_text(json.dumps({"device_id": "vm-client", "full_control": True, "limits": {"max_sessions": 1}}))
    configuration = SessionConfiguration(
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

    async def wait_for(predicate):
        async with asyncio.timeout(120):
            while not predicate():
                await asyncio.sleep(0.05)

    async def scenario():
        with (tmp_path / "daemon.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                str(binary),
                "--config",
                str(config),
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": str(tmp_path),
                    "A13N_ENVD_RUNTIME_DIR": str(runtime),
                },
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log,
            )
            device = None
            opening = None
            try:
                device = await EIPDeviceConnection.initialize(
                    StdioTransport.from_process(process),
                    expected_device_id="vm-client",
                    request_timeout=120,
                )
                opening = asyncio.create_task(device.open_session(configuration=configuration, readiness_timeout=120))
                await wait_for(lambda: bool(list(shared.glob(".eip-stage-vm-*"))))
                assert not opening.done(), "fixture must cancel during VM preparation"
                opening.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await opening
                # A cancelled client request still owns late-response cleanup.
                await wait_for(lambda: not list(runtime.glob("**/vms/session-*")))
                assert not list(shared.glob(".eip-stage-vm-*"))
                async with await device.open_session(configuration=configuration, readiness_timeout=120) as session:
                    assert session.descriptor.boundary.vm == configuration.isolation
                    assert session.descriptor.boundary.backend.value == backend.value
                    assert session.descriptor.working_directory == "/mnt/shared"
                    text = await session.client.file_read_text(
                        FileReadTextParams(
                            context=EIPCallContext(operation_id="read-sentinel"),
                            path=EIPPath(path="/mnt/shared/sentinel"),
                            line_limit=10,
                            max_line_length=100,
                        )
                    )
                    assert text.text == "host workspace"
                assert not list(runtime.glob("**/vms/session-*"))
                assert not list(shared.glob(".eip-stage-vm-*"))
                assert (shared / "sentinel").read_text() == "host workspace"
            finally:
                if opening is not None and not opening.done():
                    opening.cancel()
                    with suppress(asyncio.CancelledError):
                        await opening
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
