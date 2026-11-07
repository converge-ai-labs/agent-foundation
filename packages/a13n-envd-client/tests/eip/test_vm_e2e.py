"""Opt-in Linux KVM tests for the public Python client and embedded VM workers."""

import asyncio
import json
import os
import shutil
from contextlib import suppress
from pathlib import Path

import pytest
from a13n_envd_client import EIPDeviceConnection, StdioTransport
from a13n_envd_client.eip.v1 import (
    EIPCallContext,
    EIPPath,
    ErrorType,
    FileReadTextParams,
    GrantAccess,
    SessionConfiguration,
    VmBackend,
    VmIsolation,
    VmMount,
)
from a13n_envd_client.errors import EIPMethodError


@pytest.mark.parametrize(
    ("backend", "failure"),
    [
        (VmBackend.BOXLITE, "cancel"),
        (VmBackend.MICROSANDBOX, "cancel"),
        (VmBackend.BOXLITE, "bootstrap_space"),
        (VmBackend.BOXLITE, "timeout"),
    ],
)
def test_vm_failed_open_reclaims_storage_and_allows_replacement(tmp_path: Path, backend: VmBackend, failure: str):
    if backend.value not in os.environ.get("A13N_ENVD_VM_TEST_BACKENDS", "").split(","):
        pytest.skip("set A13N_ENVD_VM_TEST_BACKENDS and a VM-enabled A13N_ENVD_TEST_BINARY")
    binary = Path(os.environ["A13N_ENVD_TEST_BINARY"]).resolve()
    if failure == "bootstrap_space":
        # Trailing ELF padding reproduces a large embedded release artifact without
        # making the test depend on the build profile's executable size.
        padded = tmp_path / "large-envd"
        shutil.copy2(binary, padded)
        with padded.open("ab") as artifact:
            for _ in range(max(0, 373 - padded.stat().st_size // (1024 * 1024))):
                artifact.write(os.urandom(1024 * 1024))
        binary = padded
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "sentinel").write_text("host workspace")
    runtime = tmp_path / "runtime"
    config = tmp_path / "envd.json"
    config.write_text(
        json.dumps(
            {
                "device_id": "vm-client",
                "full_control": True,
                "limits": {"max_sessions": 1},
                "vm_startup_timeout_ms": 600000,
            }
        )
    )
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
                    "A13N_ENVD_VM_STARTUP_TIMEOUT_MS": "1" if failure == "timeout" else "120000",
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
                if failure == "cancel":
                    opening = asyncio.create_task(
                        device.open_session(configuration=configuration, readiness_timeout=120)
                    )
                    await wait_for(lambda: bool(list(shared.glob(".eip-stage-vm-*"))))
                    assert not opening.done(), "fixture must cancel during VM preparation"
                    opening.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await opening
                elif failure == "timeout":
                    with pytest.raises(EIPMethodError) as caught:
                        await device.open_session(configuration=configuration, readiness_timeout=10)
                    assert caught.value.error.data.error_type == ErrorType.TIMEOUT
                    assert "VM" in caught.value.error.message
                else:
                    small = configuration.model_copy(
                        update={"isolation": configuration.isolation.model_copy(update={"memory_mib": 512})}
                    )
                    with pytest.raises(EIPMethodError) as caught:
                        await device.open_session(configuration=small, readiness_timeout=120)
                    assert caught.value.error.data.error_type == ErrorType.INTERNAL_ERROR
                    message = caught.value.error.message
                    for expected in (
                        "bootstrap_copy",
                        "memory_mib=512",
                        f"worker_bytes={binary.stat().st_size}",
                        "increase memory_mib",
                    ):
                        assert expected in message
                    assert "No space left on device" in (tmp_path / "daemon.log").read_text()
                # Failed/cancelled admission still owns physical cleanup.
                await wait_for(lambda: not list(runtime.glob("**/vms/session-*")))
                assert not list(shared.glob(".eip-stage-vm-*"))
                if failure == "timeout":
                    # The Device-wide VM budget stays short. Native admission is
                    # unaffected and proves the failed VM released the only slot.
                    async with await device.open_session(
                        configuration=SessionConfiguration(working_directory=str(shared))
                    ) as session:
                        text = await session.client.file_read_text(
                            FileReadTextParams(
                                context=EIPCallContext(operation_id="read-native-replacement"),
                                path=EIPPath(path=str(shared / "sentinel")),
                                line_limit=10,
                                max_line_length=100,
                            )
                        )
                        assert text.text == "host workspace"
                    return
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
