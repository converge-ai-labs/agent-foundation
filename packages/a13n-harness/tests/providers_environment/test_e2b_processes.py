"""Native commands through the real locked SDK event consumer, without cloud credentials."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a13n_environment.commands import (
    ArgvCommand,
    CommandEnvironment,
    CommandLimits,
    CommandRequest,
    ShellCommand,
)
from a13n_environment.e2b.commands import GuestCommands
from a13n_environment.e2b.configuration import E2BEnvironmentConfiguration
from a13n_environment.e2b.processes import E2BProcesses
from a13n_environment.errors import EnvironmentProviderError
from a13n_environment.models import EnvironmentError
from a13n_environment.retention import EnvironmentOutputPolicy
from e2b.envd.process import process_pb
from e2b.sandbox.commands.main import ProcessInfo
from e2b.sandbox_async.commands.command_handle import AsyncCommandHandle
from protobuf import Oneof
from pydantic import SecretStr

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class NativeCommands:
    """Native jobs outlive each independently attached SDK event stream."""

    def __init__(self):
        self.jobs = {}
        self.streams = {}
        self.calls = []
        self.handles = []
        self.next_pid = 10

    async def run(self, script, **kwargs):
        self.calls.append(("run", script, kwargs))
        pid = self.next_pid
        self.next_pid += 1
        self.jobs[pid] = script
        handle = await self._attach(pid, kwargs)
        if script in {"immediate", "burst"}:
            for _ in range(1000 if script == "burst" else 1):
                self.emit(pid, stdout=b"x" * (1024 if script == "burst" else 200))
            await asyncio.sleep(0)
        return handle

    async def connect(self, pid, **kwargs):
        self.calls.append(("connect", pid, kwargs))
        assert pid in self.jobs
        return await self._attach(pid, kwargs)

    async def _attach(self, pid, kwargs):
        queue = asyncio.Queue()
        self.streams.setdefault(pid, []).append(queue)

        async def events():
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        return
                    yield item
            finally:
                self.streams[pid].remove(queue)

        async def kill():
            return await self.kill(pid)

        handle = AsyncCommandHandle(
            pid, kill, events(), on_stdout=kwargs.get("on_stdout"), on_stderr=kwargs.get("on_stderr")
        )
        self.handles.append(handle)
        return handle

    def emit(self, pid, *, stdout=None, stderr=None, exit_code=None):
        payload = []
        for name, data in (("stdout", stdout), ("stderr", stderr)):
            if data is not None:
                payload.append(Oneof("data", process_pb.ProcessEvent.DataEvent(output=Oneof(name, data))))
        if exit_code is not None:
            self.jobs.pop(pid, None)
            payload.append(Oneof("end", process_pb.ProcessEvent.EndEvent(exit_code=exit_code)))
        for event in payload:
            for stream in self.streams.get(pid, ()):
                stream.put_nowait(process_pb.StartResponse(event=process_pb.ProcessEvent(event=event)))
        if exit_code is not None:
            self.drop(pid)

    def drop(self, pid):
        for queue in self.streams.get(pid, ()):
            queue.put_nowait(None)

    async def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return [
            ProcessInfo(pid, None, "/bin/bash", ["-l", "-c", script], {"SECRET": "never-project"}, "/private")
            for pid, script in self.jobs.items()
        ]

    async def kill(self, pid, **kwargs):
        self.calls.append(("kill", pid))
        if pid not in self.jobs:
            return False
        self.emit(pid, exit_code=137)
        return True

    async def send_stdin(self, pid, data, **kwargs):
        self.calls.append(("stdin", pid, data))

    async def close_stdin(self, pid, **kwargs):
        self.calls.append(("close_stdin", pid))


def adapter(native, **configuration):
    commands = GuestCommands(SimpleNamespace(commands=native), E2BEnvironmentConfiguration(**configuration))
    commands.generation = "generation-sandbox-1"
    commands.mount_id = "mount-test"
    commands.files = AsyncMock(return_value={"path": "/home/user"})
    return E2BProcesses(commands, "env-test")


def request(script="job", **kwargs):
    return CommandRequest(command=ShellCommand(profile_id="default", script=script), output_policy=policy(), **kwargs)


def policy():
    return EnvironmentOutputPolicy(max_inline_bytes=1024, max_output_bytes=4096, overflow="truncate")


async def tick():
    for _ in range(20):
        await asyncio.sleep(0)


async def test_native_start_nonzero_and_sdk_text_not_original_bytes():
    native = NativeCommands()
    process = adapter(native)
    started = await process.start(request())
    native.emit(10, stdout=b"hello\xff", stderr=b"error", exit_code=7)
    info = await process.wait(started.process.handle, condition="initial_terminal", timeout_seconds=1)
    output = await process.read_output(info.handle, policy=policy())
    assert info.status.exit_code == 7
    assert info.status.cleanup is None
    assert output.stdout.chunks[0].data == "hello\ufffd".encode()
    assert output.stdout.capture.origin == "sdk_text"
    assert output.stdout.capture.produced_bytes is None
    assert output.stdout.capture.producer_complete is None
    assert output.stdout.capture.observation_closed
    assert native.calls[0][1] == "job"
    assert native.calls[0][2]["timeout"] == 0
    await process.close()
    assert not any(call[0] == "kill" for call in native.calls)


async def test_released_adapter_can_discover_and_reconnect_without_replaying_command():
    native = NativeCommands()
    first = adapter(native)
    started = await first.start(request())
    native.emit(10, stdout=b"old")
    await tick()
    await first.release(started.process.handle)
    assert 10 in native.jobs
    second = adapter(native)
    listing = await second.list(limit=1)
    assert len(listing.processes) == 1
    assert not any(call[0] == "connect" for call in native.calls)
    info = listing.processes[0]
    assert info.output is None
    assert "SECRET" not in repr(info)
    assert "private" not in repr(info)
    await second.read_output(info.handle, policy=policy())
    native.emit(10, stdout=b"new")
    await tick()
    output = await second.read_output(info.handle, policy=policy())
    assert output.stdout.chunks[0].data == b"new"
    assert output.stdout.capture.coverage == "partial"
    assert output.stdout.capture.reason == "reattached"
    assert len([call for call in native.calls if call[0] == "run"]) == 1
    await second.close()


async def test_transient_reconnect_preserves_accumulated_offsets_and_handle():
    native = NativeCommands()
    process = adapter(native)
    started = await process.start(request())
    handle = started.process.handle
    native.emit(10, stdout=b"first")
    await tick()
    native.drop(10)
    await tick()
    inspected = await process.inspect(handle)
    assert inspected.status.phase == "running"
    assert not any(call[0] == "connect" for call in native.calls)
    await process.read_output(handle, policy=policy())
    native.emit(10, stdout=b"second")
    await tick()
    output = await process.read_output(handle, stdout_start_offset=5, policy=policy())
    assert output.process.handle == handle
    assert output.stdout.chunks[0].start_offset == 5
    assert output.stdout.chunks[0].data == b"second"
    assert output.stdout.capture.available_end == 11
    assert output.stdout.capture.coverage == "partial"
    await process.close()


async def test_immediate_output_cap_detaches_real_sdk_without_kill_or_budget_reset():
    native = NativeCommands()
    process = adapter(native, max_observation_bytes=16)
    started = await process.start(request("immediate"))
    await tick()
    assert 10 in native.jobs
    assert native.streams[10] == []
    output = await process.read_output(started.process.handle, policy=policy())
    assert output.stdout.chunks[0].data == b"x" * 16
    assert output.stdout.capture.reason == "observation_limit"
    assert output.stdout.capture.observation_closed
    native_size = len(native.handles[0].stdout)
    for _ in range(3):
        await process.inspect(started.process.handle)
        await process.wait(started.process.handle, condition="initial_terminal", timeout_seconds=0.01)
        native.emit(10, stdout=b"later")
    assert len(native.handles[0].stdout) == native_size
    assert not any(call[0] in {"connect", "kill"} for call in native.calls)
    native.jobs.clear()
    assert (await process.inspect(started.process.handle)).status.phase == "missing"
    assert (await process.inspect(started.process.handle)).status.exit_code is None
    await process.close()


async def test_wait_cancellation_does_not_cancel_shared_sdk_consumer():
    native = NativeCommands()
    process = adapter(native)
    started = await process.start(request())
    waiter = asyncio.create_task(process.wait(started.process.handle, condition="initial_terminal", timeout_seconds=30))
    await tick()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    native.emit(10, stdout=b"survived", exit_code=0)
    await tick()
    output = await process.read_output(started.process.handle, policy=policy())
    assert output.process.status.exit_code == 0
    assert output.stdout.chunks[0].data == b"survived"
    await process.close()


async def test_active_capacity_excludes_closed_history_and_discovery():
    native = NativeCommands()
    process = adapter(native, max_active_observations=1)
    started = await process.start(request())
    native.jobs.update({pid: "secret command" for pid in range(1000, 1100)})
    listed = await process.list(limit=50)
    assert listed.has_more
    assert len(listed.processes) == 50
    assert not any(call[0] == "connect" for call in native.calls)
    with pytest.raises(EnvironmentError, match="capacity"):
        await process.start(request())
    with pytest.raises(EnvironmentError, match="capacity"):
        await process.read_output(listed.processes[1].handle, policy=policy())
    assert len([call for call in native.calls if call[0] == "run"]) == 1
    native.emit(10, stdout=b"history", exit_code=0)
    await tick()
    for pid in range(11, 211):
        await process.start(request())
        native.emit(pid, stdout=b"ok", exit_code=0)
        await tick()
        assert process._observations.active == 0
    assert (await process.inspect(started.process.handle)).status.exit_code == 0
    assert (await process.read_output(started.process.handle, policy=policy())).stdout.capture.inline == b"history"
    for record in process._processes.values():
        if record.observation is not None:
            assert record.observation.native is None
            assert record.observation.consumer is None
    await process.close()
    assert process._observations.retained_bytes == 0
    assert not process._observations.closed


@pytest.mark.parametrize(
    "change",
    [
        {"limits": CommandLimits(wall_time_seconds=1)},
        {"limits": CommandLimits(stdin_bytes=10)},
        {"environment": CommandEnvironment(unset=("SECRET",))},
        {"command": ArgvCommand(executable="/bin/true")},
        {"command": ShellCommand(profile_id="default", script="true", login=False)},
    ],
)
async def test_unsupported_guarantees_fail_before_native_dispatch(change):
    native = NativeCommands()
    process = adapter(native)
    with pytest.raises(EnvironmentError) as error:
        await process.start(request().model_copy(update=change))
    assert error.value.code == "environment_unsupported"
    assert native.calls == []
    await process.close()


@pytest.mark.parametrize("before_publication", [False, True])
async def test_cap_backpressures_a_ready_sdk_event_batch(before_publication):
    native = NativeCommands()
    process = adapter(native, max_observation_bytes=16)
    started = await process.start(request("burst" if before_publication else "job"))
    if not before_publication:
        for _ in range(1000):
            native.emit(10, stdout=b"x" * 1024)
    await tick()
    output = await process.read_output(started.process.handle, policy=policy())
    assert output.stdout.capture.captured_bytes == 16
    # At most the single event that reached the cap is retained by the real SDK.
    assert len(native.handles[0].stdout) == 1024
    assert native.streams[10] == []
    assert 10 in native.jobs
    await process.close()


@pytest.mark.parametrize("maximum, expected, complete", [(4096, 2000, True), (1500, 1500, False)])
async def test_foreground_materializes_beyond_page_size_before_releasing(maximum, expected, complete):
    native = NativeCommands()
    process = adapter(native)
    capture_policy = EnvironmentOutputPolicy(max_inline_bytes=1024, max_output_bytes=maximum, overflow="truncate")
    execution = asyncio.create_task(process.exec(request().model_copy(update={"output_policy": capture_policy})))
    await tick()
    native.emit(10, stdout=b"x" * 2000, exit_code=0)
    result = await execution
    assert result.status.exit_code == 0
    assert result.output.stdout.inline == b"x" * expected
    assert result.output.stdout.available_end == result.output.stdout.captured_bytes == expected
    assert result.output.stdout.content_complete is complete
    assert result.output.stdout.reference is None
    assert process._processes == {}
    await process.close()


async def test_native_kill_acceptance_does_not_depend_on_followup_inventory():
    native = NativeCommands()
    process = adapter(native)
    started = await process.start(request())
    native.list = AsyncMock(side_effect=RuntimeError("inventory unavailable"))
    result = await process.kill(started.process.handle)
    assert result.receipt.outcome == "succeeded"
    assert result.process.status.phase == "unknown"
    native.list.assert_not_called()
    await process.close()


async def test_retained_budget_evicts_oldest_closed_output_but_preserves_status_and_offsets():
    native = NativeCommands()
    process = adapter(native, max_retained_output_bytes=8)
    first = await process.start(request())
    native.emit(10, stdout=b"first", stderr=b"err", exit_code=7)
    await tick()
    second = await process.start(request())
    native.emit(11, stdout=b"second", exit_code=0)
    await tick()
    old = await process.read_output(first.process.handle, policy=policy())
    assert old.process.status.exit_code == 7
    assert old.stdout.capture.reason == "observation_evicted"
    assert old.stdout.capture.coverage == "partial"
    assert old.stdout.capture.observation_closed
    assert not old.stdout.capture.content_complete
    assert old.stdout.capture.available_start == old.stdout.capture.available_end == 5
    assert old.stderr.capture.available_start == old.stderr.capture.available_end == 3
    assert old.stdout.capture.captured_bytes == 0
    assert old.stdout.chunks == ()
    recent = await process.read_output(second.process.handle, policy=policy())
    assert recent.stdout.capture.inline == b"second"
    assert recent.stdout.capture.content_complete
    assert process._observations.retained_bytes == 6
    assert not any(call[0] in {"kill", "connect"} for call in native.calls)
    await process.release(second.process.handle)
    assert process._observations.retained_bytes == 0
    await process.close()


async def test_evicted_disconnected_log_reconnects_without_resetting_offsets_or_command_budget():
    native = NativeCommands()
    process = adapter(native, max_observation_bytes=10, max_retained_output_bytes=8)
    first = await process.start(request())
    native.emit(10, stdout=b"first")
    await tick()
    native.drop(10)
    await tick()
    assert process._observations.active == 0
    second = await process.start(request())
    native.emit(11, stdout=b"second", exit_code=0)
    await tick()
    old = await process.read_output(first.process.handle, policy=policy())
    assert old.stdout.capture.available_start == old.stdout.capture.available_end == 5
    assert old.stdout.capture.reason == "observation_evicted"
    native.emit(10, stdout=b"123456789")
    await tick()
    page = await process.read_output(first.process.handle, stdout_start_offset=5, policy=policy())
    assert page.stdout.chunks[0].start_offset == 5
    assert page.stdout.capture.inline == b"12345"
    assert page.stdout.capture.available_start == 5
    assert page.stdout.capture.available_end == 10
    assert page.stdout.capture.reason == "observation_limit"
    assert page.stdout.capture.observation_closed
    assert process._observations.active == 0
    assert process._observations.retained_bytes == 5
    assert (await process.inspect(second.process.handle)).status.exit_code == 0
    calls = len([call for call in native.calls if call[0] == "connect"])
    await process.wait(first.process.handle, condition="initial_terminal", timeout_seconds=0)
    assert len([call for call in native.calls if call[0] == "connect"]) == calls
    assert 10 in native.jobs
    await process.close()


async def test_aggregate_pressure_caps_only_new_observation_when_all_other_logs_are_active():
    native = NativeCommands()
    process = adapter(native, max_retained_output_bytes=8)
    first = await process.start(request())
    native.emit(10, stdout=b"123456")
    await tick()
    second = await process.start(request())
    native.emit(11, stdout=b"abcdef")
    await tick()
    one = await process.read_output(first.process.handle, policy=policy())
    two = await process.read_output(second.process.handle, policy=policy())
    assert one.stdout.capture.inline == b"123456"
    assert not one.stdout.capture.observation_closed
    assert two.stdout.capture.inline == b"ab"
    assert two.stdout.capture.reason == "observation_limit"
    assert two.stdout.capture.observation_closed
    assert process._observations.active == 1
    assert process._observations.retained_bytes == 8
    assert set(native.jobs) == {10, 11}
    await process.close()


async def test_failed_or_cancelled_admission_returns_active_reservation():
    native = NativeCommands()
    process = adapter(native, max_active_observations=1)
    original_run = native.run
    native.run = AsyncMock(side_effect=RuntimeError("start unavailable"))
    with pytest.raises(EnvironmentProviderError):
        await process.start(request())
    assert process._observations.active == 0
    native.run = original_run
    started = await process.start(request())
    native.drop(10)
    await tick()
    assert process._observations.active == 0
    original_connect = native.connect
    native.connect = AsyncMock(side_effect=RuntimeError("connect unavailable"))
    with pytest.raises(EnvironmentProviderError):
        await process.read_output(started.process.handle, policy=policy())
    assert process._observations.active == 0

    dispatched = asyncio.Event()

    async def pending_connect(*args, **kwargs):
        dispatched.set()
        await asyncio.Event().wait()

    native.connect = pending_connect
    waiting = asyncio.create_task(process.read_output(started.process.handle, policy=policy()))
    await dispatched.wait()
    assert process._observations.active == 1
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert process._observations.active == 0
    native.connect = original_connect
    await process.read_output(started.process.handle, policy=policy())
    assert process._observations.active == 1
    # Closing immediately after publication also releases a not-yet-scheduled owner.
    await process.close()
    assert process._observations.active == 0
    assert process._observations.retained_bytes == 0
    assert 10 in native.jobs


@pytest.mark.parametrize("evicted", [False, True])
async def test_reconnect_backpressures_a_ready_batch_before_handle_publication(evicted):
    native = NativeCommands()
    process = adapter(native, max_observation_bytes=16, max_retained_output_bytes=8)
    first = await process.start(request())
    native.emit(10, stdout=b"first")
    await tick()
    native.drop(10)
    await tick()
    if evicted:
        await process.start(request())
        native.emit(11, stdout=b"second", exit_code=0)
        await tick()
    original_connect = native.connect

    async def burst_connect(pid, **kwargs):
        handle = await original_connect(pid, **kwargs)
        for _ in range(1000):
            native.emit(pid, stdout=b"x" * 1024)
        await asyncio.sleep(0)
        return handle

    native.connect = burst_connect
    await process.read_output(first.process.handle, policy=policy())
    await tick()
    assert len(native.handles[-1].stdout) == 1024
    assert native.streams[10] == []
    assert process._observations.active == 0
    assert process._observations.retained_bytes <= 8
    assert 10 in native.jobs
    await process.close()


async def test_metadata_guard_is_separate_from_active_and_output_budgets(monkeypatch):
    monkeypatch.setattr("a13n_environment.e2b.processes._MAX_PROCESS_RECORDS", 2)
    native = NativeCommands()
    process = adapter(native)
    first = await process.start(request())
    native.emit(10, exit_code=0)
    await tick()
    await process.start(request())
    native.emit(11, exit_code=0)
    await tick()
    assert process._observations.active == process._observations.retained_bytes == 0
    with pytest.raises(EnvironmentError, match="reference capacity"):
        await process.start(request())
    assert len([call for call in native.calls if call[0] == "run"]) == 2
    assert (await process.inspect(first.process.handle)).status.exit_code == 0
    native.jobs[20] = "external job"
    with pytest.raises(EnvironmentError, match="reference capacity"):
        await process.list(limit=50)
    with pytest.raises(EnvironmentError, match="reference capacity"):
        await process.rebind(process._handle("20").identity, output_policy=policy())
    assert len(process._processes) == 2
    await process.release(first.process.handle)
    await process.start(request())
    assert len(process._processes) == 2
    await process.close()


async def test_wait_after_output_cap_polls_process_without_reattaching(monkeypatch):
    monkeypatch.setattr("a13n_environment.e2b.processes._STATUS_POLL_SECONDS", 0.01)
    native = NativeCommands()
    process = adapter(native, max_observation_bytes=16)
    started = await process.start(request("immediate"))
    await tick()
    before = asyncio.get_running_loop().time()
    info = await process.wait(started.process.handle, condition="initial_terminal", timeout_seconds=0.025)
    elapsed = asyncio.get_running_loop().time() - before
    assert info.status.phase == "running"
    assert 0.02 <= elapsed < 0.6
    assert 1 <= sum(call[0] == "list" for call in native.calls) <= 4
    assert not any(call[0] == "connect" for call in native.calls)
    await process.close()


@pytest.mark.parametrize("slow_operation", ["list", "connect"])
async def test_wait_budget_includes_native_requests(slow_operation):
    native = NativeCommands()
    process = adapter(native)
    started = await process.start(request())
    if slow_operation == "connect":
        native.drop(10)
        await tick()

    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    setattr(native, slow_operation, slow)
    before = asyncio.get_running_loop().time()
    info = await process.wait(started.process.handle, condition="initial_terminal", timeout_seconds=0.03)
    assert asyncio.get_running_loop().time() - before < 0.3
    assert info.status.phase == ("unknown" if slow_operation == "list" else "running")
    await process.close()


async def test_exec_does_not_complete_when_output_is_capped(monkeypatch):
    monkeypatch.setattr("a13n_environment.e2b.processes._STATUS_POLL_SECONDS", 0.01)
    native = NativeCommands()
    process = adapter(native, max_observation_bytes=16)
    executing = asyncio.create_task(process.exec(request("immediate")))
    # Span several status polls before the process exits.
    await asyncio.sleep(0.03)
    assert not executing.done()
    native.emit(10, exit_code=7)
    result = await asyncio.wait_for(executing, timeout=1)
    # Once detached the SDK cannot recover the exit code; do not fabricate one.
    assert result.status.phase == "missing"
    assert result.status.exit_code is None
    assert result.output.stdout.inline == b"x" * 16
    assert not any(call[0] in {"kill", "connect"} for call in native.calls)
    assert process._processes == {}


async def test_stdout_wakeups_do_not_trigger_unbounded_status_queries():
    native = NativeCommands()
    process = adapter(native)
    started = await process.start(request())

    async def produce():
        for _ in range(30):
            native.emit(10, stdout=b"x")
            await asyncio.sleep(0.002)

    producer = asyncio.create_task(produce())
    info = await process.wait(started.process.handle, condition="initial_terminal", timeout_seconds=0.15)
    await producer
    assert info.status.phase == "running"
    assert 1 <= sum(call[0] == "list" for call in native.calls) <= 3
    native.emit(10, exit_code=9)
    await tick()
    before = len(native.calls)
    info = await process.wait(started.process.handle, condition="initial_terminal", timeout_seconds=0)
    assert info.status.exit_code == 9
    assert len(native.calls) == before
    await process.close()


@pytest.mark.skipif(sys.platform == "win32", reason="The E2B guest filesystem uses POSIX paths")
async def test_project_mount_routes_guest_files_and_default_command_cwd(tmp_path, monkeypatch):
    """Real guest helper/path mapping; the cloud API and native command event source are simulated."""
    from a13n_environment.e2b.management import E2BManagement
    from a13n_environment.e2b.provider import E2B, E2BProviderRuntime
    from a13n_harness import EnvironmentMount, RunBindings
    from a13n_harness.environment.advanced import create_environment_runtime

    class Commands(NativeCommands):
        async def run(self, script, **kwargs):
            if kwargs.get("background"):
                return await super().run(script, **kwargs)
            process = await asyncio.create_subprocess_shell(script, stdout=asyncio.subprocess.PIPE)
            stdout, _ = await process.communicate()
            assert process.returncode == 0
            return SimpleNamespace(stdout=stdout.decode())

    native = Commands()
    sandbox = SimpleNamespace(sandbox_id="sandbox-shared", commands=native, is_running=AsyncMock(return_value=True))
    for directory in ("a", "b"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "README").write_text(directory)

    async def attach(*args, **kwargs):
        return sandbox

    async def close(*args, **kwargs):
        pass

    monkeypatch.setattr("a13n_environment.e2b.execution.open_sandbox", attach)
    monkeypatch.setattr("a13n_environment.e2b.execution.close_sandbox", close)
    config = E2BEnvironmentConfiguration(root=str(tmp_path), python=sys.executable)
    owner = E2BProviderRuntime(api_key=SecretStr("test-key"))
    target = E2BManagement(config, environment_id="shared", state=None, runtime=owner, operation_id="op-test")
    target._remember(sandbox.sandbox_id)
    for directory in ("a", "b", "a"):
        adapter = E2B.execution_connector(config, environment_id="shared", state=target.state, runtime=owner)
        runtime = create_environment_runtime(
            mounts={
                "workspace": EnvironmentMount(adapter, working_directory=f"/{directory}", provider_root=f"/{directory}")
            },
            default_mount="workspace",
        )
        async with runtime.bind(
            thread_id=f"thread-{directory}", run_id="run", instance=RunBindings.embedded().instance, host_refs={}
        ) as bound:
            assert (await bound.files.read_text("README")).text == directory
            assert (await bound.files.read_text("/workspace/README")).text == directory
            started = await bound.processes.start(request())
            call = native.calls[-1]
            assert call[0] == "run" and call[2]["cwd"] == str(tmp_path / directory)
            native.emit(native.next_pid - 1, exit_code=0)
            await bound.processes.wait(started.process.handle, condition="initial_terminal", timeout_seconds=1)
