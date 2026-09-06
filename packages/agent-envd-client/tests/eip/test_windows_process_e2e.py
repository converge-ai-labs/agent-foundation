"""Native Windows lifecycle through the real EIP daemon, never mocked isolation."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import sys
from contextlib import suppress
from pathlib import Path

import pytest
from a13n_envd_client import EIPSession, EIPTransportClosedError, StdioTransport
from a13n_envd_client.eip.v1 import (
    ArgvCommand,
    CommandEnvironment,
    CommandLimits,
    CommandRequest,
    EIPCallContext,
    EIPPath,
    ExecutableName,
    ProcessKillParams,
    ProcessStartParams,
    ProcessWaitCondition,
    ProcessWaitParams,
)

from .test_stdio_e2e import agent_envd_binary, start_daemon, wait_for_exit

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows Job lifecycle")


def _exited(pid: int) -> bool:
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    if not handle:
        assert ctypes.get_last_error() == 87  # process no longer exists
        return True
    try:
        return kernel.WaitForSingleObject(handle, 0) == 0
    finally:
        assert kernel.CloseHandle(handle)


@pytest.mark.parametrize("finish", ["exit", "kill", "timeout", "owner_loss", "breakaway"])
def test_windows_eip_job_lifecycle(tmp_path: Path, finish: str) -> None:
    python = Path(sys.executable).resolve()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    marker = workspace / "processes.json"
    config = tmp_path / "agent-envd.json"
    config.write_text(
        json.dumps(
            {
                "mounts": [
                    {
                        "mount_id": "workspace",
                        "native_root": str(workspace),
                        "writable": True,
                        "allow_command_execution": True,
                        "max_file_bytes": 1024 * 1024,
                        "allowed_operations": ["stat", "read_text", "write_text", "command_cwd", "executable_source"],
                    }
                ],
                "trusted_executable_roots": [str(python.parent)],
            }
        )
    )
    if finish == "breakaway":
        script = "\n".join(
            [
                "import json, os, subprocess, sys",
                "from pathlib import Path",
                "try:",
                "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB)",
                "except OSError as error:",
                "    assert error.winerror == 5, error",
                "else:",
                "    child.terminate(); child.wait(); raise AssertionError('child escaped Job')",
                f"Path({str(marker)!r}).write_text(json.dumps([os.getpid()]))",
            ]
        )
    else:
        script = "\n".join(
            [
                "import json, os, subprocess, sys, time",
                "from pathlib import Path",
                # Inherit pipes, making root-only kill or EOF-based wait insufficient.
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])",
                f"Path({str(marker)!r}).write_text(json.dumps([os.getpid(), child.pid]))",
                "sys.exit(37)" if finish == "exit" else "time.sleep(60)",
            ]
        )

    async def scenario() -> None:
        daemon = await start_daemon(agent_envd_binary(), config_path=config, runtime_dir=runtime)
        try:
            session = await EIPSession.initialize(
                StdioTransport.from_process(daemon),
                expected_environment_id="env-e2e",
                required_methods=("process.start", "process.kill", "process.wait"),
                request_timeout=15,
            )
        except EIPTransportClosedError:
            # Preserve startup diagnostics instead of reporting only pipe EOF.
            await wait_for_exit(daemon)
            raise
        pids: list[int] = []
        try:
            request = CommandRequest(
                command=ArgvCommand(
                    kind="argv", executable_spec=ExecutableName(kind="name", name=python.name), arguments=("-c", script)
                ),
                cwd=EIPPath(mount_id="workspace", path="/"),
                environment=CommandEnvironment(set={"SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows")}),
                limits=CommandLimits(wall_time_ms=3000 if finish == "timeout" else 30_000),
            )
            started = await session.client.process_start(
                ProcessStartParams(
                    context=EIPCallContext(operation_id="start-job"),
                    request=request,
                )
            )
            async with asyncio.timeout(10):
                while not marker.exists():
                    await asyncio.sleep(0.01)
                # The file may become visible before its small write completes.
                while not pids:
                    try:
                        pids = json.loads(marker.read_text())
                    except json.JSONDecodeError:
                        await asyncio.sleep(0.01)
            if finish == "kill":
                await session.client.process_kill(
                    ProcessKillParams(
                        context=EIPCallContext(operation_id="kill-job"),
                        handle=started.process.handle,
                    )
                )
            elif finish == "owner_loss":
                daemon.kill()
                await daemon.wait()
            if finish != "owner_loss":
                result = await session.client.process_wait(
                    ProcessWaitParams(
                        context=EIPCallContext(operation_id="wait-job", timeout_ms=10_000),
                        handle=started.process.handle,
                        condition=ProcessWaitCondition.TREE_CLEANED,
                    )
                )
                assert result.process.status.cleanup.value == "complete"
                if finish in {"exit", "breakaway"}:
                    assert result.process.status.exit_code == (37 if finish == "exit" else 0)
                if finish == "timeout":
                    assert result.process.status.phase.value == "timed_out"
            async with asyncio.timeout(10):
                while not all(_exited(pid) for pid in pids):
                    await asyncio.sleep(0.02)
        finally:
            with suppress(Exception):
                await session.close()
            if daemon.returncode is None:
                await wait_for_exit(daemon)

    asyncio.run(scenario())
