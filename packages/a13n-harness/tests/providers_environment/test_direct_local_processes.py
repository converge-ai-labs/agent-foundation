"""Native process lifecycle tests shared by Windows and POSIX CI."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from a13n_harness.providers.environment.commands import (
    ArgvCommand,
    CommandEnvironment,
    CommandLimits,
    CommandRequest,
    ShellCommand,
)
from a13n_harness.providers.environment.direct_local.processes import LocalProcessManager
from a13n_harness.providers.environment.direct_local.provider import DIRECT_LOCAL
from a13n_harness.providers.environment.models import EnvironmentError
from a13n_harness.providers.environment.retention import EnvironmentOutputPolicy

pytestmark = pytest.mark.anyio
OUTPUT = EnvironmentOutputPolicy(max_inline_bytes=4096, max_output_bytes=65536, overflow="retain")


@asynccontextmanager
async def _processes(
    root: Path,
    *,
    powershell: bool = False,
    max_wall_time_seconds: float = 10,
    inherit_environment: bool = False,
    allowed_environment_keys: tuple[str, ...] | None = ("SYSTEMROOT", "PATH"),
) -> AsyncIterator[LocalProcessManager]:
    provider = DIRECT_LOCAL
    profiles = []
    if powershell:
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if executable is None:
            pytest.skip("PowerShell is unavailable")
        profiles = [
            {
                "profile_id": "powershell",
                "executable": executable,
                "dialect": "powershell",
                "fixed_arguments": ["-NoLogo", "-NoProfile", "-NonInteractive"],
            }
        ]
    configuration = provider.validate_environment(
        {
            "root": {"path": str(root)},
            "allowed_executables": [str(Path(sys.executable).resolve())],
            "inherit_environment": inherit_environment,
            "allowed_environment_keys": list(allowed_environment_keys)
            if allowed_environment_keys is not None
            else None,
            "shell_profiles": profiles,
            "terminate_grace_seconds": 0.2,
            "max_wall_time_seconds": max_wall_time_seconds,
        },
    )
    environment = provider.construct(
        configuration=configuration, environment_id="native-test", state=None, runtime=None
    )
    await environment.enter(mount_id="root")
    try:
        await environment.prepare()
        processes = environment.operations.processes
        assert isinstance(processes, LocalProcessManager)
        yield processes
    finally:
        await environment.close()


def _request(script: str, **kwargs: object) -> CommandRequest:
    return CommandRequest(
        command=ArgvCommand(executable=str(Path(sys.executable).resolve()), arguments=("-c", script)),
        environment=CommandEnvironment(
            set={key: os.environ[key] for key in ("SYSTEMROOT", "PATH") if key in os.environ}
        ),
        output_policy=OUTPUT,
        **kwargs,
    )


async def test_native_argv_unicode_stdin_stderr_and_exit(tmp_path: Path) -> None:
    async with _processes(tmp_path) as processes:
        result = await processes.exec(
            _request(
                "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.stderr.buffer.write(b'error'); sys.exit(7)",
                initial_stdin='Unicode: 中文 "quoted"\n'.encode(),
            )
        )
        assert result.status.exit_code == 7
        assert result.status.cleanup == "complete"
        assert result.output.stdout.inline == 'Unicode: 中文 "quoted"\n'.encode()
        assert result.output.stderr.inline == b"error"


async def test_native_background_stdin_and_output(tmp_path: Path) -> None:
    async with _processes(tmp_path) as processes:
        started = await processes.start(
            _request(
                "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
                keep_stdin_open=True,
            )
        )
        await processes.write_stdin(started.process.handle, b"first\n")
        await processes.write_stdin(started.process.handle, b"second\n", close_after_write=True)
        terminal = await processes.wait(started.process.handle, condition="tree_cleaned", timeout_seconds=5)
        assert terminal.output is not None
        assert terminal.output.stdout.inline == b"first\nsecond\n"
        await processes.release(started.process.handle)


@pytest.mark.parametrize("ending", ["root_exit", "timeout", "kill", "cancel", "close"])
async def test_native_owned_descendants_are_cleaned(tmp_path: Path, ending: str) -> None:
    # The child would write after its owner ends and inherits its output handles.
    marker = tmp_path / "escaped"
    child = "import time,pathlib; time.sleep(2); pathlib.Path('escaped').write_text('leaked')"
    parent = (
        "import subprocess,sys,pathlib,time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "pathlib.Path('started').touch(); " + ("pass" if ending == "root_exit" else "time.sleep(30)")
    )
    async with _processes(tmp_path) as processes:
        limits = CommandLimits(wall_time_seconds=0.5 if ending == "timeout" else 10)
        if ending == "cancel":
            task = asyncio.create_task(processes.exec(_request(parent, limits=limits)))
        else:
            started = await processes.start(_request(parent, limits=limits))
        async with asyncio.timeout(5):
            while not (tmp_path / "started").exists():
                await asyncio.sleep(0.01)
        if ending == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif ending == "close":
            await processes.close()
        else:
            if ending == "kill":
                await processes.kill(started.process.handle)
            terminal = await processes.wait(started.process.handle, condition="tree_cleaned", timeout_seconds=5)
            assert terminal.status.cleanup == "complete"
            assert terminal.output is not None and terminal.output.stdout.producer_complete
            if ending == "timeout":
                assert terminal.status.phase == "timed_out"
    await asyncio.sleep(2.1)
    assert not marker.exists()


async def test_native_powershell_unicode_and_quoting(tmp_path: Path) -> None:
    # This checks encoding and quoting, not shell startup latency on a shared CI
    # runner. Keep a bounded startup allowance; the timeout lifecycle test above
    # independently checks its explicit 0.5-second deadline.
    async with _processes(tmp_path, powershell=True, max_wall_time_seconds=60) as processes:
        result = await processes.exec(
            CommandRequest(
                command=ShellCommand(
                    profile_id="powershell", script="Write-Output '中文 \"quoted\"'; Write-Error '中文 failure'; exit 7"
                ),
                environment=CommandEnvironment(
                    set={key: os.environ[key] for key in ("SYSTEMROOT", "PATH") if key in os.environ}
                ),
                output_policy=OUTPUT,
            )
        )
        assert result.status.exit_code == 7, (result.status, result.output)
        assert result.output.stdout.inline is not None
        assert result.output.stdout.inline.decode().strip() == '中文 "quoted"'
        assert result.output.stderr.inline is not None
        assert "中文 failure" in result.output.stderr.inline.decode()
        assert "CLIXML" not in result.output.stderr.inline.decode()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows signal contract")
async def test_windows_interrupt_is_explicitly_unsupported(tmp_path: Path) -> None:
    async with _processes(tmp_path) as processes:
        started = await processes.start(_request("import time; time.sleep(30)"))
        with pytest.raises(EnvironmentError, match="interrupt"):
            await processes.signal(started.process.handle, "interrupt")
        await processes.signal(started.process.handle, "terminate")
        terminal = await processes.wait(started.process.handle, condition="tree_cleaned", timeout_seconds=5)
        assert terminal.status.cleanup == "complete"


async def test_owned_worker_settles_before_repeated_cancellation_returns() -> None:
    from a13n_harness.providers.environment.direct_local.processes import _settle_task

    started = asyncio.Event()
    release = asyncio.Event()

    async def worker() -> str:
        started.set()
        await release.wait()
        return "settled"

    owned = asyncio.create_task(worker())
    waiter = asyncio.create_task(_settle_task(owned))
    await started.wait()
    for _ in range(3):
        waiter.cancel()
        await asyncio.sleep(0)
        assert not owned.cancelled()
        assert not waiter.done()
    release.set()
    value, cancellation = await waiter
    assert value == "settled"
    assert isinstance(cancellation, asyncio.CancelledError)


@pytest.mark.parametrize("inherit", [False, True])
async def test_native_environment_inheritance_is_opt_in_and_command_local(tmp_path, monkeypatch, inherit) -> None:
    import json

    monkeypatch.setenv("A13N_TEST_INHERITED", "parent")
    monkeypatch.setenv("A13N_TEST_REMOVED", "parent")
    script = (
        "import os,json; print(json.dumps([os.getenv(k) for k in "
        "['A13N_TEST_INHERITED','A13N_TEST_REMOVED','A13N_TEST_NEW']]))"
    )
    async with _processes(tmp_path, inherit_environment=inherit, allowed_environment_keys=None) as processes:
        baseline = _request(script)
        first = await processes.exec(baseline)
        assert json.loads(first.output.stdout.inline) == (["parent", "parent", None] if inherit else [None] * 3)
        changed = baseline.model_copy(
            update={
                "environment": CommandEnvironment(
                    set={**baseline.environment.set, "A13N_TEST_INHERITED": "override", "A13N_TEST_NEW": "new"},
                    unset=("A13N_TEST_REMOVED",),
                )
            }
        )
        result = await processes.exec(changed)
        assert json.loads(result.output.stdout.inline) == ["override", None, "new"]
        monkeypatch.setenv("A13N_TEST_INHERITED", "updated")
        again = await processes.exec(baseline)
        assert json.loads(again.output.stdout.inline) == (["updated", "parent", None] if inherit else [None] * 3)
    assert os.environ["A13N_TEST_REMOVED"] == "parent"
    assert "A13N_TEST_NEW" not in os.environ


@pytest.mark.parametrize("inherit", [False, True])
async def test_native_environment_allowlist_still_gates_explicit_changes(tmp_path, inherit) -> None:
    async with _processes(tmp_path, inherit_environment=inherit) as processes:
        for environment in (
            CommandEnvironment(set={"NOT_ALLOWED": "value"}),
            CommandEnvironment(unset=("NOT_ALLOWED",)),
        ):
            request = _request("pass").model_copy(update={"environment": environment})
            with pytest.raises(EnvironmentError, match="not allowed"):
                await processes.exec(request)


@pytest.mark.skipif(os.name != "nt", reason="Windows environment names are case-insensitive")
async def test_native_windows_environment_overrides_and_unsets_ignore_case(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("A13N_TEST_MIXED", "parent")
    async with _processes(
        tmp_path, inherit_environment=True, allowed_environment_keys=("A13N_TEST_MIXED",)
    ) as processes:
        base = _request("import os; print(os.getenv('A13N_TEST_MIXED', 'absent'))")
        changed = await processes.exec(
            base.model_copy(update={"environment": CommandEnvironment(set={"a13n_test_mixed": "changed"})})
        )
        assert changed.output.stdout.inline.strip() == b"changed"
        removed = await processes.exec(
            base.model_copy(update={"environment": CommandEnvironment(unset=("a13n_test_mixed",))})
        )
        assert removed.output.stdout.inline.strip() == b"absent"
