from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from a13n_harness.capabilities import (
    ManagedProcess,
    ProcessEvent,
    ProcessExecutionSnapshot,
    ProcessInputSnapshot,
    ProcessManager,
    ProcessOutputChunk,
    ProcessOutputPage,
    ProcessSignalSnapshot,
)
from a13n_harness.environment.commands import CommandRequest, ProcessStatus, ShellCommand
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.retention import EnvironmentOutputPolicy
from a13n_harness.state import AgentContextState
from a13n_harness.toolsets.process_manager import (
    PROCESS_STATE_ID,
    ManagedProcessState,
    ProcessManagerState,
    _ProcessRunManager,
)
from pydantic import BaseModel

pytestmark = pytest.mark.anyio

_PROCESS_STATE_VERSION = "2"


class _SteeringNotifier:
    def __init__(self, run: Any) -> None:
        self._run = run
        self.notifications: list[tuple[str, str, tuple[str, ...]]] = []

    async def notify(self, message: str, *, source: str, references: tuple[str, ...]) -> str:
        self.notifications.append((message, source, references))
        return self._run.enqueue(message, priority="asap")


class _RunContext:
    def __init__(
        self,
        state: AgentContextState,
        *,
        thread_id: str = "thread-parent",
        run_id: str = "run-parent",
    ) -> None:
        self.enqueued: list[tuple[str, str]] = []
        self.deps = SimpleNamespace(
            state=state,
            thread_id=thread_id,
            run_id=run_id,
            instance=SimpleNamespace(
                agent_instance_id="agent-parent",
                host_refs={"session_id": "session-parent"},
            ),
            _steering=_SteeringNotifier(self),
        )

    def enqueue(self, value: str, *, priority: str) -> str:
        self.enqueued.append((value, priority))
        return f"enqueue-{len(self.enqueued)}"


class _DetachedProcess:
    def __init__(
        self,
        backend_id: str,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self._backend_id = backend_id
        self.stdout = stdout
        self.stderr = stderr
        self.status = ProcessStatus(phase="running")
        self.stdin_open = True
        self.done = asyncio.Event()
        self.input_calls: list[tuple[bytes, bool]] = []
        self.signal_calls: list[str] = []
        self.kill_calls = 0
        self.close_calls = 0

    @property
    def backend_id(self) -> str:
        return self._backend_id

    def snapshot(self) -> ProcessExecutionSnapshot:
        return ProcessExecutionSnapshot(
            backend_id=self.backend_id,
            status=self.status,
            stdin_open=self.stdin_open,
            stdout_produced_bytes=len(self.stdout),
            stderr_produced_bytes=len(self.stderr),
        )

    async def inspect(self) -> ProcessExecutionSnapshot:
        return self.snapshot()

    async def wait(self) -> ProcessExecutionSnapshot:
        await self.done.wait()
        return self.snapshot()

    async def read_output(
        self,
        stdout_offset: int,
        stderr_offset: int,
        wait_seconds: float,
        max_bytes: int,
    ) -> ProcessOutputPage:
        if wait_seconds > 0 and not self.done.is_set():
            try:
                await asyncio.wait_for(self.done.wait(), wait_seconds)
            except TimeoutError:
                pass
        stdout = self.stdout[stdout_offset : stdout_offset + max_bytes]
        remaining = max(0, max_bytes - len(stdout))
        stderr = self.stderr[stderr_offset : stderr_offset + remaining]
        producer_complete = self.status.phase != "running"
        return ProcessOutputPage(
            snapshot=self.snapshot(),
            stdout=ProcessOutputChunk(
                data=stdout,
                start_offset=stdout_offset,
                available_start=0,
                available_end=len(self.stdout),
                producer_complete=producer_complete,
                content_complete=True,
            ),
            stderr=ProcessOutputChunk(
                data=stderr,
                start_offset=stderr_offset,
                available_start=0,
                available_end=len(self.stderr),
                producer_complete=producer_complete,
                content_complete=True,
            ),
        )

    async def write_stdin(self, data: bytes, close_after_write: bool) -> ProcessInputSnapshot:
        self.input_calls.append((data, close_after_write))
        self.stdout += data
        if close_after_write:
            self.stdin_open = False
        return ProcessInputSnapshot(accepted_bytes=len(data), snapshot=self.snapshot())

    async def signal(self, signal: Literal["interrupt", "terminate"]) -> ProcessSignalSnapshot:
        self.signal_calls.append(signal)
        self.status = ProcessStatus(
            phase="signaled",
            termination_reason="signal",
            signal=signal,
            cleanup="complete",
        )
        self.stdin_open = False
        self.done.set()
        return ProcessSignalSnapshot(accepted=True, snapshot=self.snapshot())

    async def kill(self) -> ProcessExecutionSnapshot:
        self.kill_calls += 1
        self.status = ProcessStatus(
            phase="cancelled",
            termination_reason="cancelled",
            signal="kill",
            cleanup="complete",
        )
        self.stdin_open = False
        self.done.set()
        return self.snapshot()

    async def force_close(self) -> None:
        self.close_calls += 1
        if not self.done.is_set():
            self.status = ProcessStatus(
                phase="cancelled",
                termination_reason="cancelled",
                signal="kill",
                cleanup="complete",
            )
            self.stdin_open = False
            self.done.set()

    def complete(self, *, exit_code: int = 0) -> None:
        self.status = ProcessStatus(
            phase="exited",
            termination_reason="exit",
            exit_code=exit_code,
            cleanup="complete",
        )
        self.stdin_open = False
        self.done.set()


class _Launcher:
    def __init__(self) -> None:
        self.created: list[_DetachedProcess] = []
        self.requests: list[tuple[CommandRequest, str | None]] = []
        self.queued_output: list[tuple[bytes, bytes]] = []

    def queue_output(self, stdout: bytes, stderr: bytes = b"") -> None:
        self.queued_output.append((stdout, stderr))

    async def __call__(
        self,
        context: Any,
        request: CommandRequest,
        alias: str | None,
    ) -> ManagedProcess:
        del context
        stdout, stderr = self.queued_output.pop(0) if self.queued_output else (b"", b"")
        process = _DetachedProcess(
            f"backend-{len(self.created) + 1}",
            stdout=stdout,
            stderr=stderr,
        )
        self.created.append(process)
        self.requests.append((request, alias))
        return process


def _request() -> CommandRequest:
    return CommandRequest(
        command=ShellCommand(profile_id="default", script="sleep 1"),
        keep_stdin_open=True,
        output_policy=EnvironmentOutputPolicy(
            max_inline_bytes=64,
            max_output_bytes=1024,
            overflow="retain",
        ),
    )


async def _stored_state(state: AgentContextState) -> ProcessManagerState:
    stored = await state.read(
        PROCESS_STATE_ID,
        ProcessManagerState,
        version=_PROCESS_STATE_VERSION,
    )
    assert stored is not None
    return stored


async def _wait_for(predicate: Callable[[], bool]) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), 2)


async def test_default_manager_keeps_canonical_processes_across_parent_runs_and_always_fires_hooks() -> None:
    launcher = _Launcher()
    events: list[ProcessEvent] = []

    def broken_hook(event: ProcessEvent) -> Any:
        del event
        raise RuntimeError("host observer failed")

    async def stable_hook(event: ProcessEvent) -> None:
        events.append(event)

    operator = ProcessManager(launcher, event_hooks=(broken_hook, stable_hook))
    state = AgentContextState()
    first = _ProcessRunManager(operator=operator)
    first_run = _RunContext(state, run_id="run-1")
    async with first.active_run(cast(Any, first_run)):
        started = await first.start(_request(), alias="workspace")

    assert started["process_id"] == "process-1"
    process = launcher.created[0]
    process.complete()
    await _wait_for(lambda: len(events) == 1)

    assert first_run.enqueued == []
    assert events[0].kind == "completion"
    assert events[0].thread_id == "thread-parent"
    assert events[0].run_id == "run-1"
    assert events[0].agent_instance_id == "agent-parent"
    assert events[0].host_refs == {"session_id": "session-parent"}
    assert events[0].process_id == "process-1"
    assert events[0].backend_id == "backend-1"
    assert (await _stored_state(state)).processes["process-1"].status.phase == "running"

    second = _ProcessRunManager(operator=operator)
    second_run = _RunContext(state, run_id="run-2")
    async with second.active_run(cast(Any, second_run)):
        reconciled = await second.status(cursor=0, limit=10)
        active = await second.start(_request(), alias=None)
        launcher.created[1].complete(exit_code=7)
        await _wait_for(lambda: len(events) == 2)

    assert reconciled["processes"][0]["status"]["phase"] == "exited"
    assert len(second_run.enqueued) == 2
    assert "process-1 has finished" in second_run.enqueued[0][0]
    assert "process-2 has finished" in second_run.enqueued[1][0]
    assert all(priority == "asap" for _, priority in second_run.enqueued)
    assert second_run.deps._steering.notifications[0][1:] == ("background_process", ("process-1",))
    assert second_run.deps._steering.notifications[1][1:] == ("background_process", ("process-2",))
    assert active["process_id"] == "process-2"
    assert events[1].process_id == "process-2"
    assert events[1].status.exit_code == 7

    stored = await _stored_state(state)
    assert stored.owner_thread_id == "thread-parent"
    assert stored.next_sequence == 3
    assert tuple(stored.processes) == ("process-1", "process-2")
    assert set(stored.processes["process-1"].model_dump()) == {
        "backend_id",
        "stdout_offset",
        "stderr_offset",
        "status",
        "stdin_open",
        "stdout_produced_bytes",
        "stderr_produced_bytes",
        "backend_lost",
    }
    await operator.force_close()


async def test_run_projection_controls_and_pages_detached_process_output_with_fixed_refs() -> None:
    launcher = _Launcher()
    launcher.queue_output(b"ready\n")
    launcher.queue_output(b"x" * 150_000, b"tail")
    launcher.queue_output(b"before-kill")
    operator = ProcessManager(launcher)
    projection = _ProcessRunManager(operator=operator)
    state = AgentContextState()
    run = _RunContext(state)

    async with projection.active_run(cast(Any, run)):
        first = await projection.start(_request(), alias="shell")
        accepted_bytes, stdin_open = await projection.write_input(
            "process-1",
            "hello",
            close_stdin=True,
        )
        first_output = await projection.wait("process-1", timeout_seconds=0)
        accepted, signaled = await projection.signal("process-1", "interrupt")

        second = await projection.start(_request(), alias=None)
        chunks: list[str] = []
        while sum(map(len, chunks)) < 150_000:
            page = await projection.wait("process-2", timeout_seconds=0)
            chunks.append(page["stdout"]["text"])

        third = await projection.start(_request(), alias=None)
        killed = await projection.kill("process-3")

    assert [first["process_id"], second["process_id"], third["process_id"]] == [
        "process-1",
        "process-2",
        "process-3",
    ]
    assert projection.resource_id("process-2") == "backend-2"
    assert launcher.requests[0][1] == "shell"
    assert (accepted_bytes, stdin_open) == (5, False)
    assert first_output["stdout"]["text"] == "ready\nhello"
    assert accepted is True
    assert signaled["status"]["phase"] == "signaled"
    assert launcher.created[0].input_calls == [(b"hello", True)]
    assert launcher.created[0].signal_calls == ["interrupt"]
    assert "".join(chunks) == "x" * 150_000
    assert len(chunks) > 1
    assert killed["status"]["phase"] == "cancelled"
    assert killed["stdout"]["text"] == "before-kill"
    assert launcher.created[2].kill_calls == 1

    stored = await _stored_state(state)
    assert stored.next_sequence == 4
    assert tuple(stored.processes) == ("process-1", "process-2", "process-3")
    assert stored.processes["process-2"].stdout_offset == 150_000
    await operator.force_close()


async def test_restored_process_state_is_lost_but_forked_owner_invalidates_refs() -> None:
    state = AgentContextState()
    await state.write(
        PROCESS_STATE_ID,
        ProcessManagerState(
            owner_thread_id="thread-original",
            next_sequence=2,
            processes={
                "process-1": ManagedProcessState(
                    backend_id="missing-backend",
                    status=ProcessStatus(phase="running"),
                    stdin_open=True,
                )
            },
        ),
        version=_PROCESS_STATE_VERSION,
    )
    fork_state = AgentContextState(await state.snapshot())

    restored_operator = ProcessManager(_Launcher())
    restored = _ProcessRunManager(operator=restored_operator)
    restored_run = _RunContext(state, thread_id="thread-original")
    async with restored.active_run(cast(Any, restored_run)):
        status = await restored.status(cursor=0, limit=10)

    restored_state = await _stored_state(state)
    assert status["processes"][0]["status"]["termination_reason"] == "backend_lost"
    assert restored_state.processes["process-1"].backend_id == "missing-backend"
    assert restored_state.processes["process-1"].backend_lost is True
    assert "can no longer be observed" in restored_run.enqueued[0][0]

    fork_launcher = _Launcher()
    fork_operator = ProcessManager(fork_launcher)
    forked = _ProcessRunManager(operator=fork_operator)
    fork_run = _RunContext(fork_state, thread_id="thread-fork")
    async with forked.active_run(cast(Any, fork_run)):
        empty = await forked.status(cursor=0, limit=10)
        started = await forked.start(_request(), alias=None)

    forked_state = await _stored_state(fork_state)
    assert empty["processes"] == []
    assert fork_run.enqueued == []
    assert started["process_id"] == "process-2"
    assert forked_state.owner_thread_id == "thread-fork"
    assert tuple(forked_state.processes) == ("process-2",)
    await restored_operator.force_close()
    await fork_operator.force_close()


async def test_process_manager_closes_launched_process_when_admission_inspection_fails() -> None:
    class InspectFailureProcess(_DetachedProcess):
        async def inspect(self) -> ProcessExecutionSnapshot:
            raise RuntimeError("inspect failed")

    process = InspectFailureProcess("backend-inspect-failure")

    async def launcher(context: Any, request: CommandRequest, alias: str | None) -> ManagedProcess:
        del context, request, alias
        return process

    async def observer(snapshot: ProcessExecutionSnapshot) -> None:
        del snapshot

    operator = ProcessManager(launcher)
    context = _RunContext(AgentContextState()).deps

    with pytest.raises(RuntimeError, match="inspect failed"):
        await operator.start(cast(Any, context), _request(), None, "process-1", observer)

    assert process.close_calls == 1
    assert process.done.is_set()
    await operator.force_close()


async def test_process_projection_does_not_refresh_after_committed_admission() -> None:
    class SecondInspectFailureProcess(_DetachedProcess):
        def __init__(self, backend_id: str) -> None:
            super().__init__(backend_id)
            self.inspect_calls = 0

        async def inspect(self) -> ProcessExecutionSnapshot:
            self.inspect_calls += 1
            if self.inspect_calls > 1:
                raise RuntimeError("post-admission inspect failed")
            return self.snapshot()

    process = SecondInspectFailureProcess("backend-accepted")

    async def launcher(context: Any, request: CommandRequest, alias: str | None) -> ManagedProcess:
        del context, request, alias
        return process

    operator = ProcessManager(launcher)
    projection = _ProcessRunManager(operator=operator)
    state = AgentContextState()
    run = _RunContext(state)

    async with projection.active_run(cast(Any, run)):
        accepted = await projection.start(_request(), alias=None)

    assert accepted["process_id"] == "process-1"
    assert accepted["status"]["phase"] == "running"
    assert process.inspect_calls == 1
    assert tuple((await _stored_state(state)).processes) == ("process-1",)
    await operator.force_close()


async def test_slow_process_hook_does_not_block_active_run_observer() -> None:
    launcher = _Launcher()
    hook_started = asyncio.Event()
    hook_release = asyncio.Event()

    async def slow_hook(event: ProcessEvent) -> None:
        del event
        hook_started.set()
        await hook_release.wait()

    operator = ProcessManager(launcher, event_hooks=(slow_hook,))
    projection = _ProcessRunManager(operator=operator)
    run = _RunContext(AgentContextState())

    async with projection.active_run(cast(Any, run)):
        await projection.start(_request(), alias=None)
        launcher.created[0].complete()
        await asyncio.wait_for(hook_started.wait(), 2)
        await _wait_for(lambda: bool(run.enqueued))

    assert "process-1 has finished" in run.enqueued[0][0]
    hook_release.set()
    await operator.force_close()


async def test_concurrent_process_drains_serialize_cursor_advancement() -> None:
    launcher = _Launcher()
    output = "".join(f"{index:06d}" for index in range(20_000))
    launcher.queue_output(output.encode())
    operator = ProcessManager(launcher)
    projection = _ProcessRunManager(operator=operator)
    state = AgentContextState()
    run = _RunContext(state)

    async with projection.active_run(cast(Any, run)):
        await projection.start(_request(), alias=None)
        first, second = await asyncio.gather(
            projection.wait("process-1", timeout_seconds=0),
            projection.wait("process-1", timeout_seconds=0),
        )

    captured_text = first["stdout"]["text"] + second["stdout"]["text"]
    stored = await _stored_state(state)
    assert captured_text == output[: len(captured_text)]
    assert stored.processes["process-1"].stdout_offset == len(captured_text.encode())
    await operator.force_close()


async def test_process_drain_rejects_noncontiguous_page_without_advancing_cursor() -> None:
    class MalformedPageProcess(_DetachedProcess):
        async def read_output(
            self,
            stdout_offset: int,
            stderr_offset: int,
            wait_seconds: float,
            max_bytes: int,
        ) -> ProcessOutputPage:
            del wait_seconds, max_bytes
            return ProcessOutputPage(
                snapshot=self.snapshot(),
                stdout=ProcessOutputChunk(
                    data=self.stdout[1:],
                    start_offset=stdout_offset + 1,
                    available_start=0,
                    available_end=len(self.stdout),
                ),
                stderr=ProcessOutputChunk(
                    start_offset=stderr_offset,
                    available_start=0,
                    available_end=len(self.stderr),
                ),
            )

    process = MalformedPageProcess("backend-malformed", stdout=b"abc")

    async def launcher(context: Any, request: CommandRequest, alias: str | None) -> ManagedProcess:
        del context, request, alias
        return process

    operator = ProcessManager(launcher)
    projection = _ProcessRunManager(operator=operator)
    state = AgentContextState()
    run = _RunContext(state)

    async with projection.active_run(cast(Any, run)):
        await projection.start(_request(), alias=None)
        with pytest.raises(EnvironmentError, match="noncontiguous output page"):
            await projection.wait("process-1", timeout_seconds=0)

    stored = await _stored_state(state)
    assert stored.processes["process-1"].stdout_offset == 0
    await operator.force_close()


async def test_stale_process_page_cannot_regress_newer_terminal_observer_state() -> None:
    class StalePageProcess(_DetachedProcess):
        def __init__(self, backend_id: str) -> None:
            super().__init__(backend_id, stdout=b"x" * 50)
            self.read_started = asyncio.Event()
            self.read_release = asyncio.Event()

        async def read_output(
            self,
            stdout_offset: int,
            stderr_offset: int,
            wait_seconds: float,
            max_bytes: int,
        ) -> ProcessOutputPage:
            page = await super().read_output(stdout_offset, stderr_offset, 0, max_bytes)
            self.read_started.set()
            await self.read_release.wait()
            return page

    process = StalePageProcess("backend-stale-page")
    completion_delivered = asyncio.Event()

    async def launcher(context: Any, request: CommandRequest, alias: str | None) -> ManagedProcess:
        del context, request, alias
        return process

    async def stable_hook(event: ProcessEvent) -> None:
        if event.kind == "completion":
            completion_delivered.set()

    operator = ProcessManager(launcher, event_hooks=(stable_hook,))
    projection = _ProcessRunManager(operator=operator)
    state = AgentContextState()
    run = _RunContext(state)

    async with projection.active_run(cast(Any, run)):
        await projection.start(_request(), alias=None)
        draining = asyncio.create_task(projection.wait("process-1", timeout_seconds=0))
        await asyncio.wait_for(process.read_started.wait(), 2)
        process.stdout = b"x" * 100
        process.complete()
        await asyncio.wait_for(completion_delivered.wait(), 2)
        await _wait_for(lambda: projection._entries["process-1"].state.status.phase == "exited")
        process.read_release.set()
        await draining
        await _wait_for(lambda: bool(run.enqueued))

    stored = await _stored_state(state)
    assert stored.processes["process-1"].status.phase == "exited"
    assert stored.processes["process-1"].stdout_produced_bytes == 100
    assert "process-1 has finished" in run.enqueued[0][0]
    await operator.force_close()


async def test_process_projection_kills_backend_when_parent_state_admission_fails() -> None:
    class FailingState(AgentContextState):
        async def write(
            self,
            capability_id: str,
            value: BaseModel,
            *,
            version: str,
        ) -> None:
            del value, version
            if capability_id == PROCESS_STATE_ID:
                raise RuntimeError("state write failed")
            raise AssertionError("unexpected state namespace")

    launcher = _Launcher()
    operator = ProcessManager(launcher)
    projection = _ProcessRunManager(operator=operator)
    run = _RunContext(FailingState())

    async with projection.active_run(cast(Any, run)):
        with pytest.raises(RuntimeError, match="state write failed"):
            await projection.start(_request(), alias=None)

    assert launcher.created[0].kill_calls == 1
    assert launcher.created[0].done.is_set()
    await operator.force_close()


async def test_process_admission_compensation_survives_repeated_cancellation() -> None:
    class FailingState(AgentContextState):
        async def write(
            self,
            capability_id: str,
            value: BaseModel,
            *,
            version: str,
        ) -> None:
            del value, version
            if capability_id == PROCESS_STATE_ID:
                raise RuntimeError("state write failed")
            raise AssertionError("unexpected state namespace")

    class BlockingKillProcess(_DetachedProcess):
        def __init__(self, backend_id: str) -> None:
            super().__init__(backend_id)
            self.kill_started = asyncio.Event()
            self.kill_release = asyncio.Event()

        async def kill(self) -> ProcessExecutionSnapshot:
            self.kill_started.set()
            await self.kill_release.wait()
            return await super().kill()

    process = BlockingKillProcess("backend-blocking-kill")

    async def launcher(context: Any, request: CommandRequest, alias: str | None) -> ManagedProcess:
        del context, request, alias
        return process

    operator = ProcessManager(launcher)
    projection = _ProcessRunManager(operator=operator)
    run = _RunContext(FailingState())

    async with projection.active_run(cast(Any, run)):
        starting = asyncio.create_task(projection.start(_request(), alias=None))
        await asyncio.wait_for(process.kill_started.wait(), 2)
        starting.cancel()
        await asyncio.sleep(0)
        starting.cancel()
        await asyncio.sleep(0)
        assert not starting.done()
        process.kill_release.set()
        with pytest.raises(asyncio.CancelledError):
            await starting

    assert process.kill_calls == 1
    assert process.done.is_set()
    await operator.force_close()


async def test_process_manager_force_close_finishes_cleanup_before_propagating_cancellation() -> None:
    class BlockingForceCloseProcess(_DetachedProcess):
        def __init__(self, backend_id: str) -> None:
            super().__init__(backend_id)
            self.close_started = asyncio.Event()
            self.close_release = asyncio.Event()

        async def force_close(self) -> None:
            self.close_started.set()
            await self.close_release.wait()
            await super().force_close()

    process = BlockingForceCloseProcess("backend-blocking-force-close")

    async def launcher(context: Any, request: CommandRequest, alias: str | None) -> ManagedProcess:
        del context, request, alias
        return process

    operator = ProcessManager(launcher)
    projection = _ProcessRunManager(operator=operator)
    run = _RunContext(AgentContextState())
    async with projection.active_run(cast(Any, run)):
        await projection.start(_request(), alias=None)

    closing = asyncio.create_task(operator.force_close())
    await asyncio.wait_for(process.close_started.wait(), 2)
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()

    process.close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert process.close_calls == 1
    assert process.done.is_set()
    await operator.force_close()


async def test_process_manager_force_close_stops_all_owned_detached_resources() -> None:
    launcher = _Launcher()
    operator = ProcessManager(launcher)
    projection = _ProcessRunManager(operator=operator)
    run = _RunContext(AgentContextState())
    async with projection.active_run(cast(Any, run)):
        await projection.start(_request(), alias=None)
        await projection.start(_request(), alias=None)

    await operator.force_close()
    await operator.force_close()

    assert [process.close_calls for process in launcher.created] == [1, 1]
    assert all(process.done.is_set() for process in launcher.created)


async def test_process_manager_force_close_reports_failure_after_cleaning_every_process() -> None:
    class FailingForceCloseProcess(_DetachedProcess):
        async def force_close(self) -> None:
            await super().force_close()
            raise RuntimeError("process cleanup failed")

    processes = [
        FailingForceCloseProcess("backend-failing-close"),
        _DetachedProcess("backend-successful-close"),
    ]
    queued = list(processes)

    async def launcher(context: Any, request: CommandRequest, alias: str | None) -> ManagedProcess:
        del context, request, alias
        return queued.pop(0)

    operator = ProcessManager(launcher)
    projection = _ProcessRunManager(operator=operator)
    run = _RunContext(AgentContextState())
    async with projection.active_run(cast(Any, run)):
        await projection.start(_request(), alias=None)
        await projection.start(_request(), alias=None)

    with pytest.raises(BaseExceptionGroup, match="Managed process cleanup failed"):
        await operator.force_close()

    assert [process.close_calls for process in processes] == [1, 1]
    assert await operator.inspect(cast(Any, run.deps), "backend-failing-close") is None
    assert await operator.inspect(cast(Any, run.deps), "backend-successful-close") is None
