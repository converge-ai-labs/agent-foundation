from __future__ import annotations

import asyncio
import re
import sys
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_environment_provider import (
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
    DirectLocalShellProfile,
    EnvironmentOutputCapture,
    EnvironmentOutputSegment,
    ProcessStreamRead,
)
from a13n_harness import RunBindings
from a13n_harness.environment import (
    EnvironmentAction,
    EnvironmentError,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.providers import BoundEnvironment, EnvironmentRuntimeMount
from a13n_harness.toolsets.process_manager import (
    _await_cleanup_shielded,
    _project_stream,
    _RunProcessController,
    _validate_stream,
)
from a13n_harness.toolsets.shell import ShellToolset

from .environment_helpers import DirectLocalEnvironmentProviderBinding

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(sys.platform == "win32", reason="Direct Local process groups require POSIX"),
]

_PROCESS_ID = re.compile(r"^process-[0-9a-f]{4}-[1-9][0-9]*$")


class _Steering:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, str, tuple[str, ...]]] = []
        self.notified = asyncio.Event()

    async def notify(
        self,
        message: str,
        *,
        source: str,
        references: tuple[str, ...],
    ) -> str:
        self.notifications.append((message, source, references))
        self.notified.set()
        return f"notification-{len(self.notifications)}"


class _ToolDeps:
    def __init__(self) -> None:
        self._steering = _Steering()

    async def _spill_tool_result(self, data: bytes, *, suffix: str) -> None:
        del data, suffix
        return None


def _ctx() -> Any:
    return SimpleNamespace(deps=_ToolDeps())


@asynccontextmanager
async def _bound_process_environment(root: Path) -> AsyncIterator[BoundEnvironment]:
    runtime = create_environment_runtime(
        mounts={
            "local": EnvironmentRuntimeMount(
                binding=DirectLocalEnvironmentProviderBinding(
                    DirectLocalProviderConfiguration(
                        root=DirectLocalRootConfiguration(path=root),
                        shell_profiles=(
                            DirectLocalShellProfile(
                                profile_id="default",
                                executable=Path("/bin/sh"),
                            ),
                        ),
                        allowed_executables=frozenset({Path(sys.executable).resolve()}),
                        terminate_grace_seconds=0.2,
                    ),
                    environment_id="run-process-test",
                ),
                permission_ceiling=EnvironmentPermissionSet(
                    operations=frozenset(EnvironmentAction),
                ),
                working_directory="/",
            )
        },
        default_mount="local",
    )
    instance = RunBindings.embedded().instance
    async with runtime.bind(
        thread_id="thread-test",
        run_id="run-test",
        instance=instance,
        host_refs={},
    ) as environment:
        await runtime._activate()
        yield environment


async def test_shell_exec_returns_no_id_for_quick_completion_and_run_id_for_live_process(
    tmp_path: Path,
) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        toolset = ShellToolset(environment, process_capable=True)
        ctx = _ctx()

        quick = await toolset.shell_exec(ctx, "printf quick", yield_time_seconds=2)
        live = await toolset.shell_exec(ctx, "printf ready; sleep 30", yield_time_seconds=0)

        assert quick["ok"] is True
        assert "process_id" not in quick
        assert quick["stdout"]["text"] == "quick"
        assert quick["stdout"]["next_offset"] == 5
        assert live["ok"] is True
        process_id = cast(str, live["process_id"])
        assert _PROCESS_ID.fullmatch(process_id)
        assert live["status"]["phase"] not in {
            "exited",
            "signaled",
            "timed_out",
            "cancelled",
            "failed",
        }

        stopped = await toolset.shell_signal(ctx, process_id, "kill")
        final = await toolset.shell_wait(
            ctx,
            process_id,
            stdout_offset=live["stdout"]["next_offset"],
            stderr_offset=live["stderr"]["next_offset"],
            timeout_seconds=2,
        )
        assert stopped["ok"] is True
        assert set(stopped) == {"ok", "process_id", "accepted", "stdin_open", "status"}
        assert final["ok"] is True
        await toolset.close()


async def test_shell_wait_repeats_output_for_repeated_explicit_offsets(tmp_path: Path) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        toolset = ShellToolset(environment, process_capable=True)
        ctx = _ctx()
        started = await toolset.shell_exec(
            ctx,
            "printf first; sleep 0.1; printf second",
            yield_time_seconds=0,
        )
        process_id = cast(str, started["process_id"])

        first, repeated = await asyncio.gather(
            toolset.shell_wait(
                ctx,
                process_id,
                stdout_offset=0,
                stderr_offset=0,
                timeout_seconds=2,
            ),
            toolset.shell_wait(
                ctx,
                process_id,
                stdout_offset=0,
                stderr_offset=0,
                timeout_seconds=2,
            ),
        )
        continued = await toolset.shell_wait(
            ctx,
            process_id,
            stdout_offset=first["stdout"]["next_offset"],
            stderr_offset=first["stderr"]["next_offset"],
            timeout_seconds=0,
        )

        assert first["stdout"]["requested_offset"] == 0
        assert first["stdout"]["text"] == "firstsecond"
        assert repeated["stdout"] == first["stdout"]
        assert continued["stdout"]["requested_offset"] == len(b"firstsecond")
        assert continued["stdout"]["text"] == ""
        assert continued["stdout"]["next_offset"] == len(b"firstsecond")
        await toolset.close()


async def test_shell_input_and_signal_mutate_without_returning_output(tmp_path: Path) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        toolset = ShellToolset(environment, process_capable=True)
        ctx = _ctx()
        started = await toolset.shell_exec(
            ctx,
            'IFS= read -r value; printf "got:%s" "$value"',
            yield_time_seconds=0,
        )
        process_id = cast(str, started["process_id"])

        written = await toolset.shell_input(ctx, process_id, "hello\n", close_stdin=True)
        final = await toolset.shell_wait(
            ctx,
            process_id,
            stdout_offset=0,
            stderr_offset=0,
            timeout_seconds=2,
        )

        assert written["ok"] is True
        assert written["accepted_bytes"] == len(b"hello\n")
        assert written["stdin_open"] is False
        assert set(written) == {"ok", "process_id", "accepted_bytes", "stdin_open", "status"}
        assert final["stdout"]["text"] == "got:hello"
        await toolset.close()


async def test_live_completion_notifies_once_but_quick_completion_does_not(tmp_path: Path) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        toolset = ShellToolset(environment, process_capable=True)
        ctx = _ctx()

        async def run_commands() -> tuple[dict[str, Any], dict[str, Any]]:
            quick = await toolset.shell_exec(ctx, "printf quick", yield_time_seconds=2)
            live = await toolset.shell_exec(ctx, "sleep 0.05; printf done", yield_time_seconds=0)
            await asyncio.wait_for(ctx.deps._steering.notified.wait(), timeout=2)
            return cast(dict[str, Any], quick), cast(dict[str, Any], live)

        quick, live = await toolset.wrap_run(ctx, handler=run_commands)
        await asyncio.sleep(0)

        assert "process_id" not in quick
        process_id = cast(str, live["process_id"])
        assert ctx.deps._steering.notifications == [
            (
                f"Background process {process_id} has finished. "
                "Call shell_wait with your last returned stdout_offset and stderr_offset "
                "to inspect its final output.",
                "background_process",
                (process_id,),
            )
        ]
        await toolset.close()


async def test_cancellation_after_publication_keeps_process_owned_until_controller_close(
    tmp_path: Path,
) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        controller = _RunProcessController(environment)
        request = ShellToolset._command_request(
            "sleep 30",
            cwd=None,
            environment=None,
            timeout_seconds=None,
            keep_stdin_open=True,
        )
        start_task = asyncio.create_task(controller.start(request, alias=None, yield_time_seconds=30))
        while not controller._entries:
            await asyncio.sleep(0)

        start_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start_task

        assert len(controller._entries) == 1
        process_id = next(iter(controller._entries))
        assert controller.resource_id(process_id)
        handle = controller._entries[process_id].handle

        await controller.close()
        assert not controller._entries
        with pytest.raises(EnvironmentError):
            await environment.processes.inspect(handle)


async def test_process_references_are_run_local_and_cleanup_is_idempotent(tmp_path: Path) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        first = _RunProcessController(environment)
        second = _RunProcessController(environment)
        request = ShellToolset._command_request(
            "sleep 30",
            cwd=None,
            environment=None,
            timeout_seconds=None,
            keep_stdin_open=True,
        )
        started = await first.start(request, alias=None, yield_time_seconds=0)
        process_id = cast(str, started["process_id"])

        with pytest.raises(EnvironmentError) as foreign:
            await second.wait(
                process_id,
                stdout_offset=0,
                stderr_offset=0,
                timeout_seconds=0,
            )
        assert foreign.value.code == "environment_not_found"

        await first.close()
        await first.close()
        await second.close()


async def test_admission_failure_after_start_compensates_with_kill_and_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        processes = environment.processes
        original_start = processes.start
        original_kill = processes.kill
        original_release = processes.release
        handles: list[Any] = []
        killed: list[Any] = []
        released: list[Any] = []

        async def record_start(*args: Any, **kwargs: Any) -> Any:
            result = await original_start(*args, **kwargs)
            handles.append(result.process.handle)
            return result

        async def record_kill(handle: Any) -> Any:
            killed.append(handle)
            return await original_kill(handle)

        async def record_release(handle: Any) -> Any:
            released.append(handle)
            return await original_release(handle)

        monkeypatch.setattr(processes, "start", record_start)
        monkeypatch.setattr(processes, "kill", record_kill)
        monkeypatch.setattr(processes, "release", record_release)

        class FailingEntries(OrderedDict[str, Any]):
            def __setitem__(self, key: str, value: Any) -> None:
                del key, value
                raise RuntimeError("entry publication failed")

        controller = _RunProcessController(environment)
        controller._entries = FailingEntries()
        request = ShellToolset._command_request(
            "sleep 30",
            cwd=None,
            environment=None,
            timeout_seconds=None,
            keep_stdin_open=True,
        )

        with pytest.raises(RuntimeError, match="entry publication failed"):
            await controller.start(request, alias=None, yield_time_seconds=0)

        assert len(handles) == 1
        assert killed == handles
        assert released == handles
        with pytest.raises(EnvironmentError):
            await processes.inspect(handles[0])
        await controller.close()


def test_output_projection_reports_retention_omission_without_false_advancement() -> None:
    capture = EnvironmentOutputCapture(
        kind="retained",
        producer_complete=False,
        content_complete=False,
        produced_bytes=12,
        captured_bytes=7,
        dropped_bytes=5,
        available_start=5,
        available_end=12,
    )
    stream = ProcessStreamRead(
        chunks=(EnvironmentOutputSegment(start_offset=5, data=b"retained"[:7]),),
        next_cursor=None,
        capture=capture,
    )

    _validate_stream(stream, requested_offset=0)
    projected = _project_stream(
        stream,
        requested_offset=0,
        data=b"ret",
        full_page_bytes=7,
    )

    assert projected["requested_offset"] == 0
    assert projected["start_offset"] == 5
    assert projected["next_offset"] == 8
    assert projected["available_start"] == 5
    assert projected["available_end"] == 12
    assert projected["omitted_before_bytes"] == 5
    assert projected["content_complete"] is False
    assert projected["text"] == "ret"


def test_output_validation_rejects_noncontiguous_provider_page() -> None:
    capture = EnvironmentOutputCapture(
        kind="retained",
        producer_complete=False,
        content_complete=True,
        produced_bytes=8,
        captured_bytes=8,
        dropped_bytes=0,
        available_start=0,
        available_end=8,
    )
    malformed = ProcessStreamRead(
        chunks=(EnvironmentOutputSegment(start_offset=1, data=b"invalid"),),
        next_cursor=None,
        capture=capture,
    )

    with pytest.raises(EnvironmentError) as error:
        _validate_stream(malformed, requested_offset=0)
    assert error.value.code == "environment_provider_failure"


async def test_repeated_cancellation_cannot_cancel_accepted_cleanup() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    inner_cancelled = False

    async def cleanup() -> None:
        nonlocal inner_cancelled
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            inner_cancelled = True
            raise

    owner = asyncio.create_task(_await_cleanup_shielded(cleanup()))
    await started.wait()
    owner.cancel()
    await asyncio.sleep(0)
    owner.cancel()
    await asyncio.sleep(0)
    assert inner_cancelled is False
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await owner
    assert inner_cancelled is False


async def test_quick_large_output_is_recoverable_through_disclosure(tmp_path: Path) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        spilled: list[bytes] = []

        class SpillDeps(_ToolDeps):
            async def _spill_tool_result(self, data: bytes, *, suffix: str) -> str:
                assert suffix == ".json"
                spilled.append(data)
                return "/workspace/.a13n/tool-results/quick.json"

        toolset = ShellToolset(environment, process_capable=True)
        ctx = SimpleNamespace(deps=SpillDeps())
        result = await toolset.shell_exec(
            ctx,
            "head -c 30000 /dev/zero | tr '\\0' x",
            yield_time_seconds=2,
        )

        assert result["ok"] is True
        assert "process_id" not in result
        assert result["disclosure"]["output_file_path"] == "/workspace/.a13n/tool-results/quick.json"
        assert len(spilled) == 1
        assert spilled[0].count(b"x") >= 30_000
        await toolset.close()


async def test_stale_running_snapshot_cannot_replace_terminal_status(tmp_path: Path) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        controller = _RunProcessController(environment)
        request = ShellToolset._command_request(
            "sleep 30",
            cwd=None,
            environment=None,
            timeout_seconds=None,
            keep_stdin_open=True,
        )
        started = await controller.start(request, alias=None, yield_time_seconds=0)
        process_id = cast(str, started["process_id"])
        entry = controller._entries[process_id]
        running = entry.latest

        await controller.signal(process_id, "kill")
        assert entry.latest.status.phase in {"exited", "signaled", "timed_out", "cancelled", "failed"}
        terminal = entry.latest

        controller._adopt_info(entry, running, baseline=running)
        assert entry.latest is terminal
        await controller.close()
