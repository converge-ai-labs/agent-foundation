from __future__ import annotations

import asyncio
import re
import sys
from collections import OrderedDict
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from a13n_harness import AgentContext, AgentSpec, HarnessBuilder, RunBindings
from a13n_harness._tool_execution import RunToolExecutions
from a13n_harness.environment import (
    EnvironmentAction,
    EnvironmentError,
    EnvironmentMount,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.advanced import create_environment_runtime
from a13n_harness.environment.commands import ProcessDiscovery, ProcessStreamRead
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.environment.retention import EnvironmentOutputCapture, EnvironmentOutputSegment
from a13n_harness.providers.environment.direct_local.configuration import (
    DirectLocalEnvironmentConfiguration,
    DirectLocalRootConfiguration,
    DirectLocalShellProfile,
)
from a13n_harness.tool_execution import ToolExecutionConfig
from a13n_harness.toolsets.process_manager import (
    _await_cleanup_shielded,
    _ProcessController,
    _project_stream,
    _validate_stream,
)
from a13n_harness.toolsets.shell import ShellToolset
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from .environment_helpers import DirectLocalSource

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

    async def discard_notifications(self, *, source: str, references: tuple[str, ...]) -> None:
        self.notifications[:] = [notice for notice in self.notifications if notice[1:] != (source, references)]


class _ToolDeps:
    def __init__(self) -> None:
        self._steering = _Steering()
        self._tool_executions = RunToolExecutions()
        self._tool_executions.config = ToolExecutionConfig(mode="auto", foreground_wait_seconds=0)

    async def _spill_tool_result(self, data: bytes, *, suffix: str) -> None:
        del data, suffix
        return None


def _ctx(wait_seconds: float = 0) -> Any:
    events = []

    async def emit(event) -> None:
        events.append(event)

    deps = _ToolDeps()
    deps._tool_executions.config = ToolExecutionConfig(mode="auto", foreground_wait_seconds=wait_seconds)
    return SimpleNamespace(deps=deps, emit=emit, events=events, tool_call_id=None)


@asynccontextmanager
async def _bound_process_environment(
    root: Path,
    operations: frozenset[EnvironmentAction] | None = None,
) -> AsyncGenerator[BoundEnvironment]:
    runtime = create_environment_runtime(
        mounts={
            "local": EnvironmentMount(
                source=DirectLocalSource(
                    DirectLocalEnvironmentConfiguration(
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
                    operations=frozenset(EnvironmentAction) if operations is None else operations,
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
        toolset = ShellToolset(environment)
        ctx = _ctx()

        quick = await toolset.shell_exec(_ctx(2), "printf quick")
        live = await toolset.shell_exec(ctx, "printf ready; sleep 30")

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
        toolset = ShellToolset(environment)
        ctx = _ctx()
        started = await toolset.shell_exec(
            ctx,
            "printf first; sleep 0.1; printf second",
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
        toolset = ShellToolset(environment)
        ctx = _ctx()
        started = await toolset.shell_exec(
            ctx,
            'IFS= read -r value; printf "got:%s" "$value"',
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
        toolset = ShellToolset(environment)
        ctx = _ctx()

        async def run_commands() -> tuple[dict[str, Any], dict[str, Any]]:
            quick = await toolset.shell_exec(_ctx(2), "printf quick")
            live = await toolset.shell_exec(ctx, "sleep 0.05; printf done")
            await asyncio.wait_for(ctx.deps._steering.notified.wait(), timeout=2)
            return cast(dict[str, Any], quick), cast(dict[str, Any], live)

        quick, live = await toolset.wrap_run(ctx, handler=run_commands)
        await asyncio.sleep(0)

        assert "process_id" not in quick
        process_id = cast(str, live["process_id"])
        callbacks = [event for event in ctx.events if event.callback]
        assert len(callbacks) == 1
        assert callbacks[0].process_id == process_id
        assert callbacks[0].phase == "exited"
        assert callbacks[0].exit_code == 0
        assert ctx.deps._steering.notifications == [
            (
                f"Background process {process_id} has exited. Call shell_wait for available output.",
                "background_process",
                (process_id,),
            )
        ]
        await toolset.close()


@pytest.mark.parametrize("notice_first", [False, True])
async def test_final_wait_withdraws_completion_notice_in_either_order(tmp_path: Path, notice_first: bool) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        controller = _ProcessController(environment)
        ctx = _ctx()
        callback_started = asyncio.Event()
        release_callback = asyncio.Event()

        async def emit(event: Any) -> None:
            ctx.events.append(event)
            if event.callback:
                callback_started.set()
                await release_callback.wait()

        ctx.emit = emit
        async with controller.active_run(ctx):
            request = ShellToolset._command_request(
                "read value; printf done",
                cwd=None,
                environment=None,
                execution_timeout_seconds=None,
                keep_stdin_open=True,
            )
            started = await controller.start(request, alias=None, yield_time_seconds=0)
            process_id = started["process_id"]
            await controller.write_input(process_id, data="go\n", close_stdin=True)
            await asyncio.wait_for(callback_started.wait(), timeout=3)
            if notice_first:
                release_callback.set()
                await asyncio.wait_for(ctx.deps._steering.notified.wait(), timeout=2)
                assert len(ctx.deps._steering.notifications) == 1
            result = await controller.wait(process_id, stdout_offset=0, stderr_offset=0, timeout_seconds=2)
            assert result["stdout"]["text"] == "done"
            release_callback.set()
            await controller._entries[process_id].watcher
            assert ctx.deps._steering.notifications == []
            repeated = await controller.wait(process_id, stdout_offset=0, stderr_offset=0, timeout_seconds=0)
            assert repeated == result
            assert len([event for event in ctx.events if event.callback]) == 1
        await controller.close()


async def test_partial_wait_keeps_notice_until_last_available_page(tmp_path: Path) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        controller = _ProcessController(environment)
        ctx = _ctx()
        async with controller.active_run(ctx):
            request = ShellToolset._command_request(
                "read value; head -c 20000 /dev/zero | tr '\\0' x",
                cwd=None,
                environment=None,
                execution_timeout_seconds=None,
                keep_stdin_open=True,
            )
            started = await controller.start(request, alias=None, yield_time_seconds=0)
            process_id = started["process_id"]
            await controller.write_input(process_id, data="go\n", close_stdin=True)
            await asyncio.wait_for(ctx.deps._steering.notified.wait(), timeout=3)
            page = await controller.wait(process_id, stdout_offset=0, stderr_offset=0, timeout_seconds=0)
            assert page["stdout"]["next_offset"] < page["stdout"]["available_end"]
            assert len(ctx.deps._steering.notifications) == 1
            text = page["stdout"]["text"]
            while page["stdout"]["next_offset"] < page["stdout"]["available_end"]:
                page = await controller.wait(
                    process_id,
                    stdout_offset=page["stdout"]["next_offset"],
                    stderr_offset=0,
                    timeout_seconds=0,
                )
                text += page["stdout"]["text"]
            assert text == "x" * 20000
            assert ctx.deps._steering.notifications == []
        await controller.close()


@pytest.mark.parametrize("open_stream", ["stdout", "stderr"])
async def test_terminal_wait_keeps_notice_while_output_observation_is_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    open_stream: str,
) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        controller = _ProcessController(environment)
        ctx = _ctx()
        async with controller.active_run(ctx):
            started = await controller.start(
                ShellToolset._command_request(
                    "read value; printf done",
                    cwd=None,
                    environment=None,
                    execution_timeout_seconds=None,
                    keep_stdin_open=True,
                ),
                alias=None,
                yield_time_seconds=0,
            )
            process_id = started["process_id"]
            await controller.wait(process_id, stdout_offset=0, stderr_offset=0, timeout_seconds=0)
            assert not controller._entries[process_id].completion_observed
            await controller.write_input(process_id, "go\n", close_stdin=True)
            await asyncio.wait_for(ctx.deps._steering.notified.wait(), timeout=3)
            observed, output = await controller._observe(
                controller._entries[process_id],
                stdout_offset=0,
                stderr_offset=0,
                wait_seconds=0,
            )
            stream = output.stdout if open_stream == "stdout" else output.stderr
            stream = stream.model_copy(
                update={
                    "capture": stream.capture.model_copy(
                        update={
                            "observation_closed": False,
                            "producer_complete": False,
                            "content_complete": False,
                        }
                    )
                }
            )
            monkeypatch.setattr(
                controller,
                "_observe",
                AsyncMock(
                    return_value=(
                        observed,
                        output.model_copy(update={open_stream: stream}),
                    )
                ),
            )
            await controller.wait(process_id, stdout_offset=0, stderr_offset=0, timeout_seconds=0)
            assert len(ctx.deps._steering.notifications) == 1
            assert not controller._entries[process_id].completion_observed
        await controller.close()


async def test_final_wait_prevents_native_final_drain_followup(tmp_path: Path) -> None:
    calls = 0
    native_context: RunContext[AgentContext] | None = None
    process_id = ""
    async with _bound_process_environment(tmp_path) as environment:
        controller = _ProcessController(environment)

        async def start(ctx: RunContext[AgentContext]) -> str:
            nonlocal native_context, process_id
            native_context = ctx
            async with controller.active_run(ctx):
                result = await controller.start(
                    ShellToolset._command_request(
                        "read value; printf done",
                        cwd=None,
                        environment=None,
                        execution_timeout_seconds=None,
                        keep_stdin_open=True,
                    ),
                    alias=None,
                    yield_time_seconds=0,
                )
            process_id = result["process_id"]
            return process_id

        async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
            nonlocal calls
            del messages, info
            calls += 1
            if calls == 1:
                yield {0: DeltaToolCall(name="start", json_args="{}")}
                return
            assert native_context is not None
            if calls == 2:
                async with controller.active_run(native_context):
                    await controller.write_input(process_id, "go\n", close_stdin=True)
                    await asyncio.wait_for(controller._entries[process_id].watcher, timeout=3)
                    assert native_context.pending_messages
                    result = await controller.wait(process_id, stdout_offset=0, stderr_offset=0, timeout_seconds=0)
                    assert result["stdout"]["text"] == "done"
            yield "done"

        executable = HarnessBuilder().build(
            AgentSpec(),
            output_type=str,
            model=FunctionModel(stream_function=model),
            capabilities=(Capability(toolsets=[FunctionToolset([start])]),),
        )
        result = await executable.run("run the command", bindings=RunBindings.embedded())
        assert result.output_or_raise() == "done"
        assert calls == 2
        assert "Background process" not in str(result.all_messages())
        await controller.close()


@pytest.mark.parametrize("discard_between_attempts", [False, True])
async def test_withdrawal_preserves_other_inputs_across_attempt_rebind(discard_between_attempts: bool) -> None:
    from a13n_harness.capabilities.steering import SteeringBridge
    from a13n_harness.state import AgentContextState
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    bridge = SteeringBridge(AgentContextState(), run_id="run-test", retain_inputs=False, events=AsyncMock())
    first = RunContext(deps=None, model=TestModel(), usage=RunUsage(), pending_messages=[])
    await bridge.bind(first)
    await bridge.notify("observed", source="background_process", references=("process-one",))
    await bridge.notify("other process", source="background_process", references=("process-two",))
    await bridge.notify("other source", source="async_subagent", references=("process-one",))
    await bridge.steer("user input", input_id="user-one")
    bridge.unbind(first, owned=True)
    if discard_between_attempts:
        await bridge.discard_notifications(source="background_process", references=("process-one",))
    second = RunContext(deps=None, model=TestModel(), usage=RunUsage(), pending_messages=[])
    await bridge.bind(second)
    await bridge.discard_notifications(source="background_process", references=("process-one",))
    assert second.pending_messages is not None
    assert len(second.pending_messages) == 3
    assert "observed" not in str(second.pending_messages)
    assert "other process" in str(second.pending_messages)
    assert "other source" in str(second.pending_messages)
    assert "user input" in str(second.pending_messages)
    # Delivery is immutable: withdrawal only changes pending lifecycle notices.
    delivered = [message for pending in second.pending_messages for message in pending.messages]
    second.pending_messages.clear()
    await bridge.resolve_delivered(delivered)
    await bridge.discard_notifications(source="background_process", references=("process-two",))
    assert "other process" in str(delivered)
    assert not bridge._pending_requests


async def test_cancellation_after_publication_releases_observation_without_killing_process(
    tmp_path: Path,
) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        controller = _ProcessController(environment)
        request = ShellToolset._command_request(
            "sleep 30",
            cwd=None,
            environment=None,
            execution_timeout_seconds=None,
            keep_stdin_open=True,
        )
        start_task = asyncio.create_task(controller.start(request, alias=None, yield_time_seconds=30))
        while not controller._entries:
            await asyncio.sleep(0)

        start_task.cancel()
        result = await start_task
        assert result["process_id"] in controller._entries
        assert controller._entries[result["process_id"]].model_visible.is_set()

        assert len(controller._entries) == 1
        process_id = next(iter(controller._entries))
        assert controller.resource_id(process_id)
        handle = controller._entries[process_id].handle

        await controller.close()
        assert not controller._entries
        assert (await environment.processes.inspect(handle)).status.phase == "running"


async def test_cancellation_during_admission_settles_original_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        controller = _ProcessController(environment)
        started, publish = asyncio.Event(), asyncio.Event()
        original_start = environment.processes.start
        calls = 0

        async def blocked_start(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = await original_start(*args, **kwargs)
            started.set()
            await publish.wait()
            return result

        monkeypatch.setattr(environment.processes, "start", blocked_start)
        request = ShellToolset._command_request(
            "sleep 30", cwd=None, environment=None, execution_timeout_seconds=None, keep_stdin_open=True
        )
        task = asyncio.create_task(controller.start(request, alias=None, yield_time_seconds=30))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        publish.set()
        result = await task
        assert calls == 1
        entry = controller._entries[result["process_id"]]
        assert entry.model_visible.is_set()
        await controller.close()
        assert (await environment.processes.inspect(entry.handle)).status.phase == "running"


@pytest.mark.parametrize("mode", ["inline", "auto"])
@pytest.mark.parametrize("wait_seconds", [None, 0, 0.125])
async def test_shell_uses_common_foreground_wait_policy(tmp_path: Path, monkeypatch, mode, wait_seconds):
    async with _bound_process_environment(tmp_path) as environment:
        toolset = ShellToolset(environment)
        ctx = _ctx()
        config = (
            ToolExecutionConfig(mode=mode)
            if wait_seconds is None
            else ToolExecutionConfig(mode=mode, foreground_wait_seconds=wait_seconds)
        )
        ctx.deps._tool_executions.config = config
        start = AsyncMock(wraps=toolset._process_controller.start)
        monkeypatch.setattr(toolset._process_controller, "start", start)
        await toolset.shell_exec(ctx, "echo value")
        assert start.await_args.kwargs["yield_time_seconds"] == (5.0 if wait_seconds is None else wait_seconds)
        await toolset.close()


async def test_shell_without_execution_runtime_returns_live_reference_after_default_wait(tmp_path, monkeypatch):
    async with _bound_process_environment(tmp_path) as environment:
        toolset = ShellToolset(environment)
        ctx = _ctx()
        ctx.deps.tool_execution = None
        ctx.deps._tool_executions.config = ToolExecutionConfig()
        original_wait = environment.processes.wait
        wait = AsyncMock(side_effect=EnvironmentError("Wait expired", code="environment_timeout"))
        monkeypatch.setattr(environment.processes, "wait", wait)
        result = await toolset.shell_exec(ctx, "while [ ! -f release ]; do sleep 0.01; done; printf done")
        assert result["ok"] is True, result
        assert wait.await_count == 1
        assert wait.await_args.kwargs["timeout_seconds"] == 5.0
        assert result["status"]["phase"] == "running"
        process_id = result["process_id"]
        assert process_id is not None
        (tmp_path / "release").touch()
        await original_wait(
            toolset._process_controller._entries[process_id].handle, condition="initial_terminal", timeout_seconds=2
        )
        completed = await toolset.shell_wait(ctx, process_id, timeout_seconds=2)
        assert completed["status"]["phase"] == "exited"
        assert completed["stdout"]["text"] == "done"
        await toolset.close()


async def test_process_references_are_run_local_and_cleanup_is_idempotent(tmp_path: Path) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        first = _ProcessController(environment)
        second = _ProcessController(environment)
        request = ShellToolset._command_request(
            "sleep 30",
            cwd=None,
            environment=None,
            execution_timeout_seconds=None,
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


async def test_admission_failure_after_start_releases_without_implying_kill(
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

        controller = _ProcessController(environment)
        controller._entries = FailingEntries()
        request = ShellToolset._command_request(
            "sleep 30",
            cwd=None,
            environment=None,
            execution_timeout_seconds=None,
            keep_stdin_open=True,
        )

        with pytest.raises(RuntimeError, match="entry publication failed"):
            await controller.start(request, alias=None, yield_time_seconds=0)

        assert len(handles) == 1
        assert killed == []
        assert released == handles
        assert (await processes.inspect(handles[0])).status.phase == "running"
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
                spilled.append(data)
                return f"/workspace/.a13n/tool-results/quick{suffix}"

        toolset = ShellToolset(environment)
        ctx = SimpleNamespace(deps=SpillDeps(), tool_call_id=None)
        ctx.deps._tool_executions.config = ToolExecutionConfig(mode="auto", foreground_wait_seconds=2)
        result = await toolset.shell_exec(
            ctx,
            "head -c 30000 /dev/zero | tr '\\0' x",
        )

        assert result["ok"] is True
        assert "process_id" not in result
        assert result["disclosure"]["output_file_path"] == "/workspace/.a13n/tool-results/quick.json"
        assert result["disclosure"]["text_file_paths"] == {"/stdout/text": "/workspace/.a13n/tool-results/quick.txt"}
        assert len(spilled) == 2
        assert spilled[0].count(b"x") >= 30_000
        assert spilled[1] == b"x" * 30_000
        await toolset.close()


async def test_stale_running_snapshot_cannot_replace_terminal_status(tmp_path: Path) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        controller = _ProcessController(environment)
        request = ShellToolset._command_request(
            "sleep 30",
            cwd=None,
            environment=None,
            execution_timeout_seconds=None,
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


async def test_observation_does_not_require_stdin_signals_or_standalone_outputs(tmp_path: Path) -> None:
    operations = frozenset(
        {
            EnvironmentAction.SHELL_EXEC,
            EnvironmentAction.PROCESS_START,
            EnvironmentAction.PROCESS_INSPECT,
            EnvironmentAction.PROCESS_READ_OUTPUT,
            EnvironmentAction.PROCESS_WAIT,
            EnvironmentAction.PROCESS_RELEASE,
        }
    )
    async with _bound_process_environment(tmp_path, operations) as environment:
        toolset = ShellToolset(environment)
        assert set(toolset.get_toolset().tools) == {"shell_exec", "shell_info", "shell_wait"}
        result = await toolset.shell_exec(_ctx(), "sleep 0.05; printf observed")
        assert result["ok"]
        ref = result["process_id"]
        final = await toolset.shell_wait(_ctx(), ref, timeout_seconds=2)
        assert final["ok"]
        assert final["stdout"]["text"] == "observed"
        inspected = await toolset.shell_info(_ctx(), ref, alias="local")
        assert inspected["ok"]
        assert "stdout" not in inspected
        listing = await toolset.shell_info(_ctx())
        assert not listing["ok"]
        assert listing["error"]["code"] == "environment_denied"
        await toolset.close()


async def test_invented_alias_does_not_start_process_and_valid_interactive_flow_still_works(
    tmp_path: Path,
) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        toolset = ShellToolset(environment)
        ctx = _ctx()
        try:
            first = await toolset.shell_exec(_ctx(1), "printf ordinary")
            assert first["ok"] and first["stdout"]["text"] == "ordinary"
            invalid = await toolset.shell_exec(ctx, "cat", alias="shell-smoke-input")
            assert not invalid["ok"]
            assert invalid["error"]["code"] == "environment_selection_invalid"
            assert invalid["error"]["details"]["field"] == "alias"
            assert "not a process label" in invalid["error"]["details"]["hint"]
            assert "process_id" not in invalid
            assert not toolset._process_controller._entries

            started = await toolset.shell_exec(ctx, "cat; printf 'stdin: EOF' >&2")
            assert started["ok"]
            ref = started["process_id"]
            inspected = await toolset.shell_inspect(ctx, ref)
            assert inspected["ok"]
            written = await toolset.shell_input(ctx, ref, "hello\\n", close_stdin=True)
            assert written["ok"]
            final = await toolset.shell_wait(ctx, ref, timeout_seconds=2)
            assert final["ok"]
            assert final["status"]["exit_code"] == 0
            assert final["stdout"]["text"] == "hello\\n"
            assert final["stderr"]["text"] == "stdin: EOF"
        finally:
            await toolset.close()


async def test_foreground_capture_requires_only_shell_execution(tmp_path: Path) -> None:
    async with _bound_process_environment(tmp_path, frozenset({EnvironmentAction.SHELL_EXEC})) as environment:
        toolset = ShellToolset(environment)
        assert set(toolset.get_toolset().tools) == {"shell_exec"}
        result = await toolset.shell_exec_foreground(_ctx(), "printf captured")
        assert result["ok"]
        assert result["stdout"]["text"] == "captured"
        await toolset.close()


async def test_native_exit_is_observable_before_tree_cleanup_and_output_completion(tmp_path: Path, monkeypatch) -> None:
    from a13n_harness.providers.environment.direct_local.processes import LocalProcessManager

    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()
    original = LocalProcessManager._cleanup_group

    async def delayed_cleanup(self, pid):
        cleanup_started.set()
        await cleanup_release.wait()
        await original(self, pid)

    monkeypatch.setattr(LocalProcessManager, "_cleanup_group", delayed_cleanup)
    async with _bound_process_environment(tmp_path) as environment:
        request = ShellToolset._command_request(
            "sleep 30 & printf exited",
            cwd=None,
            environment=None,
            execution_timeout_seconds=None,
            keep_stdin_open=False,
        )
        started = await environment.processes.start(request)
        try:
            await asyncio.wait_for(cleanup_started.wait(), timeout=2)
            info = await environment.processes.wait(
                started.process.handle, condition="initial_terminal", timeout_seconds=1
            )
            assert info.status.phase == "exited"
            assert info.status.exit_code == 0
            assert info.status.cleanup == "pending"
            assert info.output is not None
            assert not info.output.stdout.producer_complete
        finally:
            cleanup_release.set()
        final = await environment.processes.wait(started.process.handle, condition="tree_cleaned", timeout_seconds=2)
        assert final.status.cleanup == "complete"
        await environment.processes.release(started.process.handle)


async def test_input_close_permission_is_checked_before_writing(tmp_path: Path, monkeypatch) -> None:
    from a13n_harness.providers.environment.direct_local.processes import LocalProcessManager

    writes = []
    original = LocalProcessManager.write_stdin

    async def observe_write(self, handle, data, **kwargs):
        writes.append(data)
        return await original(self, handle, data, **kwargs)

    monkeypatch.setattr(LocalProcessManager, "write_stdin", observe_write)
    operations = frozenset(EnvironmentAction) - {EnvironmentAction.PROCESS_CLOSE_STDIN}
    async with _bound_process_environment(tmp_path, operations) as environment:
        toolset = ShellToolset(environment)
        request = ShellToolset._command_request(
            "cat",
            cwd=None,
            environment=None,
            execution_timeout_seconds=None,
            keep_stdin_open=True,
        )
        started = await toolset._process_controller.start(request, alias=None, yield_time_seconds=0)
        result = await toolset.shell_input(_ctx(), started["process_id"], "not-written", close_stdin=True)
        assert not result["ok"]
        assert result["error"]["code"] == "environment_denied"
        assert writes == []
        await toolset.close()


async def test_input_acceptance_survives_later_status_lookup_failure(tmp_path: Path, monkeypatch) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        toolset = ShellToolset(environment)
        started = await toolset.shell_exec(_ctx(), "cat")

        async def unavailable(handle):
            raise EnvironmentError("Temporarily unavailable.", code="environment_unavailable")

        monkeypatch.setattr(environment.processes, "inspect", unavailable)
        result = await toolset.shell_input(_ctx(), started["process_id"], "accepted\n")
        assert result["ok"]
        assert result["accepted_bytes"] == len(b"accepted\n")
        assert result["status"]["phase"] == "unknown"
        await toolset.close()


async def test_discovered_reference_cleanup_tolerates_retired_mount_but_inspect_is_stale(
    tmp_path: Path, monkeypatch
) -> None:
    async with _bound_process_environment(tmp_path) as environment:
        request = ShellToolset._command_request(
            "sleep 30",
            cwd=None,
            environment=None,
            execution_timeout_seconds=None,
            keep_stdin_open=False,
        )
        started = await environment.processes.start(request)
        await environment.processes.release(started.process.handle)
        monkeypatch.setattr(
            environment.processes, "list", AsyncMock(return_value=ProcessDiscovery(processes=(started.process,)))
        )
        toolset = ShellToolset(environment)
        listing = await toolset.shell_info(_ctx())
        reference = listing["processes"][0]["process_id"]
        await environment._runtime.unmount("local")
        await asyncio.gather(*environment._retirement_tasks)
        inspected = await toolset.shell_info(_ctx(), reference)
        assert not inspected["ok"]
        assert inspected["error"]["code"] == "environment_stale_mount"
        await toolset.close()


async def test_shell_toolset_owns_binding_and_process_resources(tmp_path: Path) -> None:
    from a13n_harness.tools import HARNESS_TOOL_METADATA_KEY

    async with _bound_process_environment(tmp_path) as environment:
        toolset = ShellToolset(environment)
        tools = toolset.get_toolset().tools
        ctx = _ctx()
        command_resolver = tools["shell_exec"].metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver
        bindings = await command_resolver({"command": "cat"}, context=ctx.deps)
        assert len(bindings) == 1
        assert bindings[0].kind == "mount"
        info_resolver = tools["shell_info"].metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver
        assert await info_resolver({}, context=ctx.deps) == bindings
        started = await toolset.shell_exec(ctx, "cat")
        try:
            process_id = started["process_id"]
            for name in ("shell_info", "shell_wait", "shell_input", "shell_signal"):
                resolver = tools[name].metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver
                resources = await resolver({"process_id": process_id}, context=ctx.deps)
                assert len(resources) == 1
                assert resources[0].kind == "managed-process"
                assert resources[0].identifier == toolset.resolve_process_resource(process_id)
        finally:
            await toolset.close()


async def test_shell_resource_and_guard_callbacks_remain_overrides(tmp_path: Path) -> None:
    from a13n_harness.tools import HARNESS_TOOL_METADATA_KEY

    async def resolve(arguments, *, context):
        return ()

    async with _bound_process_environment(tmp_path) as environment:
        toolset = ShellToolset(environment, resource_resolver=lambda tool_id: resolve)
        assert all(
            tool.metadata[HARNESS_TOOL_METADATA_KEY].resource_resolver is resolve
            for tool in toolset.get_toolset().tools.values()
        )
        calls: list[str] = []
        toolset = ShellToolset(environment, execution_guard=lambda: calls.append("guard"))
        result = await toolset.shell_exec(_ctx(), "true")
        assert result["ok"] is True
        assert calls
        await toolset.close()
