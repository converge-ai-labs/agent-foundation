from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a13n_harness.environment.commands import (
    BoundProcessHandle,
    CommandRequest,
    ProcessControlResult,
    ProcessIdentity,
    ProcessInfo,
    ProcessOutputSnapshot,
    ProcessStartResult,
    ProcessStatus,
    ShellCommand,
)
from a13n_harness.environment.models import EnvironmentError, EnvironmentOperationReceipt
from a13n_harness.environment.retention import (
    BoundOutputReference,
    EnvironmentOutputCapture,
    EnvironmentOutputPolicy,
    EnvironmentOutputSegment,
    OpaqueOutputReference,
    OpaqueProcessHandle,
)
from a13n_harness.state import AgentContextState
from a13n_harness.toolsets.process_manager import (
    PROCESS_STATE_ID,
    ProcessEvent,
    ProcessManager,
    ProcessManagerState,
)

pytestmark = pytest.mark.anyio


class _RunContext:
    def __init__(self, state: AgentContextState) -> None:
        self.deps = SimpleNamespace(
            state=state,
            thread_id="thread-test",
            run_id="run-test",
            instance=SimpleNamespace(agent_instance_id="agent-instance-test"),
        )
        self.enqueued: list[tuple[str, str]] = []

    def enqueue(self, value: str, *, priority: str) -> str:
        self.enqueued.append((value, priority))
        return f"enqueue-{len(self.enqueued)}"


class _Outputs:
    def __init__(self) -> None:
        self.release_calls = 0

    async def release(self, **kwargs: Any) -> None:
        del kwargs
        self.release_calls += 1


class _Processes:
    def __init__(
        self,
        *,
        binding_version: int = 1,
        rebind_error: str | None = None,
        terminal_on_start: bool = False,
    ) -> None:
        self.identity = ProcessIdentity(
            provider_type="test",
            environment_id="environment-1",
            generation="generation-1",
            process_id="provider-process-1",
        )
        self.binding_version = binding_version
        self.handle = self._new_handle()
        self.rebind_error = rebind_error
        self.terminal_on_start = terminal_on_start
        self.completed = terminal_on_start
        self.completion = asyncio.Event()
        if terminal_on_start:
            self.completion.set()
        self.rebind_identities: list[ProcessIdentity] = []
        self.kill_calls = 0
        self.release_calls = 0
        self.wait_started = asyncio.Event()

    def _new_handle(self) -> BoundProcessHandle:
        return BoundProcessHandle(
            binding_id="binding-1",
            binding_version=self.binding_version,
            identity=self.identity,
            handle=OpaqueProcessHandle._from_payload(f"bound-{self.binding_version}"),
            observed_generation=self.identity.generation,
        )

    def refresh_binding(self) -> None:
        self.binding_version += 1
        self.handle = self._new_handle()

    @staticmethod
    def _receipt(binding_version: int = 1) -> EnvironmentOperationReceipt:
        return EnvironmentOperationReceipt(
            binding_id="binding-1",
            binding_version=binding_version,
            observed_generation="generation-1",
            operation_id="operation-1",
            stage="completed",
            outcome="succeeded",
        )

    def info(self) -> ProcessInfo:
        data = b"ready\n"
        terminal = self.completed
        stdout = EnvironmentOutputCapture(
            kind="inline",
            producer_complete=terminal,
            content_complete=True,
            produced_bytes=len(data),
            captured_bytes=len(data),
            dropped_bytes=0,
            inline=data,
            available_end=len(data),
        )
        stderr = EnvironmentOutputCapture(
            kind="empty",
            producer_complete=terminal,
            content_complete=True,
            produced_bytes=0,
            captured_bytes=0,
            dropped_bytes=0,
        )
        return ProcessInfo(
            handle=self.handle,
            status=ProcessStatus(
                phase="exited" if terminal else "running",
                termination_reason="exit" if terminal else None,
                exit_code=0 if terminal else None,
                cleanup="complete" if terminal else "pending",
            ),
            stdin_open=not terminal,
            output=ProcessOutputSnapshot(stdout=stdout, stderr=stderr),
        )

    async def start(self, request: CommandRequest, *, alias: str | None = None) -> ProcessStartResult:
        del request, alias
        return ProcessStartResult(process=self.info(), receipt=self._receipt(self.binding_version))

    async def rebind(
        self,
        identity: ProcessIdentity,
        *,
        output_policy: EnvironmentOutputPolicy,
    ) -> ProcessInfo:
        del output_policy
        self.rebind_identities.append(identity)
        if self.rebind_error is not None:
            raise EnvironmentError("rebind failed", code=self.rebind_error)
        assert identity == self.identity
        return self.info()

    async def inspect(self, handle: BoundProcessHandle) -> ProcessInfo:
        if handle != self.handle:
            raise EnvironmentError("stale handle", code="environment_stale_binding")
        return self.info()

    async def wait(
        self,
        handle: BoundProcessHandle,
        *,
        condition: str,
        timeout_seconds: float,
    ) -> ProcessInfo:
        del condition, timeout_seconds
        if handle != self.handle:
            raise EnvironmentError("stale handle", code="environment_stale_binding")
        self.wait_started.set()
        await self.completion.wait()
        self.completed = True
        return self.info()

    async def kill(self, handle: BoundProcessHandle) -> ProcessControlResult:
        assert handle == self.handle
        self.kill_calls += 1
        self.completed = True
        self.completion.set()
        return ProcessControlResult(process=self.info(), receipt=self._receipt(self.binding_version))

    async def release(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
        assert handle == self.handle
        self.release_calls += 1
        return self._receipt(self.binding_version)


class _UnavailableProcesses(_Processes):
    async def rebind(
        self,
        identity: ProcessIdentity,
        *,
        output_policy: EnvironmentOutputPolicy,
    ) -> ProcessInfo:
        del identity, output_policy
        raise EnvironmentError(
            "environment is not attached",
            code="environment_selection_invalid",
            retry_hint="dependency_change",
        )


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


def _manager(
    processes: _Processes,
    *,
    outputs: _Outputs | None = None,
    hooks: tuple[Any, ...] = (),
) -> ProcessManager:
    return ProcessManager(
        processes=cast(Any, processes),
        outputs=cast(Any, outputs or _Outputs()),
        max_reference_entries=4,
        process_event_hooks=hooks,
    )


async def _stored_state(state: AgentContextState) -> ProcessManagerState:
    snapshot = await state.snapshot()
    entry = snapshot.entries[PROCESS_STATE_ID]
    return ProcessManagerState.model_validate(entry.data)


async def _seed_running_process(state: AgentContextState) -> tuple[_Processes, ProcessManager]:
    processes = _Processes()
    manager = _manager(processes)
    run = _RunContext(state)
    async with manager.active_run(cast(Any, run)):
        started = await manager.start(_request(), alias=None)
        assert started["process_id"] == "process-1"
    return processes, manager


async def test_process_mapping_and_output_offsets_survive_agent_state_snapshot() -> None:
    state = AgentContextState()
    processes, manager = await _seed_running_process(state)

    stored = await _stored_state(state)

    assert stored.next_sequence == 2
    assert stored.processes["process-1"].identity == processes.identity
    assert stored.processes["process-1"].stdout_offset == len(b"ready\n")
    assert stored.processes["process-1"].stderr_offset == 0
    await manager.close()


async def test_restored_process_rebinds_to_a_fresh_binding_without_alias_retargeting() -> None:
    state = AgentContextState()
    original, first_manager = await _seed_running_process(state)
    await first_manager.close()
    restored = _Processes(binding_version=2)
    second_manager = _manager(restored)
    run = _RunContext(state)

    async with second_manager.active_run(cast(Any, run)):
        result = await second_manager.status(cursor=0, limit=10)

    assert result["processes"][0]["ok"] is True
    assert restored.rebind_identities
    assert all(identity == original.identity for identity in restored.rebind_identities)
    stored = await _stored_state(state)
    assert stored.processes["process-1"].binding_id == "binding-1"
    assert stored.processes["process-1"].backend_lost is False
    await second_manager.close()


async def test_unattached_environment_is_unavailable_without_deleting_process_state() -> None:
    state = AgentContextState()
    _, first_manager = await _seed_running_process(state)
    await first_manager.close()
    second_manager = _manager(_UnavailableProcesses())
    run = _RunContext(state)

    async with second_manager.active_run(cast(Any, run)):
        result = await second_manager.status(cursor=0, limit=10)

    assert result["processes"][0] == {
        "process_id": "process-1",
        "ok": False,
        "error": {"code": "environment_selection_invalid", "retry_hint": "dependency_change"},
    }
    stored = await _stored_state(state)
    assert stored.processes["process-1"].backend_lost is False
    await second_manager.close()


@pytest.mark.parametrize(
    "error_code",
    ["environment_not_found", "environment_process_generation_mismatch"],
)
async def test_missing_process_or_changed_generation_is_lazy_corrected_to_backend_lost(
    error_code: str,
) -> None:
    state = AgentContextState()
    _, first_manager = await _seed_running_process(state)
    await first_manager.close()
    second_manager = _manager(_Processes(rebind_error=error_code))
    run = _RunContext(state)

    async with second_manager.active_run(cast(Any, run)):
        result = await second_manager.status(cursor=0, limit=10)

    process = result["processes"][0]
    assert process["ok"] is True
    assert process["status"]["phase"] == "failed"
    assert process["status"]["termination_reason"] == "backend_lost"
    stored = await _stored_state(state)
    assert stored.processes["process-1"].backend_lost is True
    await second_manager.close()


async def test_rebind_topology_race_preserves_process_state_for_retry() -> None:
    state = AgentContextState()
    _, first_manager = await _seed_running_process(state)
    await first_manager.close()
    second_manager = _manager(_Processes(rebind_error="environment_stale_binding"))
    run = _RunContext(state)

    async with second_manager.active_run(cast(Any, run)):
        result = await second_manager.status(cursor=0, limit=10)

    assert result["processes"][0] == {
        "process_id": "process-1",
        "ok": False,
        "error": {"code": "environment_stale_binding", "retry_hint": "none"},
    }
    stored = await _stored_state(state)
    assert stored.processes["process-1"].backend_lost is False
    await second_manager.close()


async def test_turn_observer_waits_on_real_process_and_enqueues_completion_and_host_hook() -> None:
    state = AgentContextState()
    processes = _Processes()
    hook_events: list[ProcessEvent] = []
    hook_called = asyncio.Event()

    async def hook(event: ProcessEvent) -> None:
        hook_events.append(event)
        hook_called.set()

    manager = _manager(processes, hooks=(hook,))
    run = _RunContext(state)
    async with manager.active_run(cast(Any, run)):
        await manager.start(_request(), alias=None)
        await asyncio.wait_for(processes.wait_started.wait(), timeout=1)
        processes.completed = True
        processes.completion.set()
        await asyncio.wait_for(hook_called.wait(), timeout=1)

    assert hook_events == [
        ProcessEvent(
            kind="completion",
            thread_id="thread-test",
            run_id="run-test",
            agent_instance_id="agent-instance-test",
            process_id="process-1",
            identity=processes.identity,
            status=processes.info().status,
        )
    ]
    assert len(run.enqueued) == 1
    assert "process-1 completed" in run.enqueued[0][0]
    assert run.enqueued[0][1] == "asap"
    await manager.close()


async def test_close_cancels_turn_observation_without_killing_provider_process() -> None:
    state = AgentContextState()
    processes = _Processes()
    manager = _manager(processes)
    run = _RunContext(state)

    async with manager.active_run(cast(Any, run)):
        await manager.start(_request(), alias=None)
        await asyncio.wait_for(processes.wait_started.wait(), timeout=1)
        await manager.close()
        await manager.close()

    assert processes.kill_calls == 0
    assert processes.release_calls == 0


async def test_turn_exit_cancels_observation_until_the_next_turn() -> None:
    state = AgentContextState()
    processes = _Processes()
    hook_events: list[ProcessEvent] = []

    async def hook(event: ProcessEvent) -> None:
        hook_events.append(event)

    manager = _manager(processes, hooks=(hook,))
    run = _RunContext(state)
    async with manager.active_run(cast(Any, run)):
        await manager.start(_request(), alias=None)
        await asyncio.wait_for(processes.wait_started.wait(), timeout=1)

    processes.completed = True
    processes.completion.set()
    await asyncio.sleep(0)
    assert hook_events == []
    assert run.enqueued == []

    async with manager.active_run(cast(Any, run)):
        for _ in range(10):
            if hook_events:
                break
            await asyncio.sleep(0)

    assert len(hook_events) == 1
    assert len(run.enqueued) == 1
    await manager.close()


async def test_stale_bound_handle_rebinds_before_marking_process_lost() -> None:
    state = AgentContextState()
    processes = _Processes()
    manager = _manager(processes)
    run = _RunContext(state)

    async with manager.active_run(cast(Any, run)):
        await manager.start(_request(), alias=None)
        processes.refresh_binding()
        result = await manager.status(cursor=0, limit=10)

    assert result["processes"][0]["ok"] is True
    assert processes.rebind_identities
    stored = await _stored_state(state)
    assert stored.processes["process-1"].backend_lost is False
    await manager.close()


async def test_terminal_start_is_released_only_after_initial_output_is_projected() -> None:
    state = AgentContextState()
    processes = _Processes(terminal_on_start=True)
    manager = _manager(processes)
    run = _RunContext(state)

    async with manager.active_run(cast(Any, run)):
        started = await manager.start(_request(), alias=None)

    assert started["status"]["phase"] == "exited"
    assert started["stdout"]["text"] == "ready\n"
    assert processes.release_calls == 0
    assert (await _stored_state(state)).processes.keys() == {"process-1"}

    await manager.close()

    assert processes.release_calls == 1
    assert (await _stored_state(state)).processes == {}


async def test_terminal_release_detaches_process_before_output_and_retries_partial_cleanup() -> None:
    order: list[str] = []

    class RetainedProcesses(_Processes):
        def info(self) -> ProcessInfo:
            info = super().info()
            reference = BoundOutputReference(
                binding_id="binding-1",
                binding_version=self.binding_version,
                observed_generation=self.identity.generation,
                reference=OpaqueOutputReference._from_payload("stdout-1"),
            )
            stdout = info.output.stdout.model_copy(
                update={
                    "kind": "retained",
                    "inline": None,
                    "preview": (EnvironmentOutputSegment(start_offset=0, data=b"ready\n"),),
                    "reference": reference,
                }
            )
            return info.model_copy(update={"output": info.output.model_copy(update={"stdout": stdout})})

        async def release(self, handle: BoundProcessHandle) -> EnvironmentOperationReceipt:
            assert handle == self.handle
            order.append("process")
            self.release_calls += 1
            if self.release_calls > 1:
                raise EnvironmentError("already released", code="environment_not_found")
            return self._receipt(self.binding_version)

    class RetryOutputs(_Outputs):
        def __init__(self) -> None:
            super().__init__()
            self.failures = 1

        async def release(self, **kwargs: Any) -> None:
            del kwargs
            order.append("output")
            if self.failures:
                self.failures -= 1
                raise EnvironmentError("temporary output cleanup failure", code="environment_provider_failure")
            self.release_calls += 1

    state = AgentContextState()
    processes = RetainedProcesses(terminal_on_start=True)
    outputs = RetryOutputs()
    manager = _manager(processes, outputs=outputs)
    run = _RunContext(state)

    async with manager.active_run(cast(Any, run)):
        started = await manager.start(_request(), alias=None)
        assert started["stdout"]["text"] == "ready\n"
        assert (await _stored_state(state)).processes.keys() == {"process-1"}

        first_status = await manager.status(cursor=0, limit=10)
        status = await manager.status(cursor=0, limit=10)

    assert first_status["processes"][0]["error"]["code"] == "environment_cleanup_pending"
    assert status["processes"] == []
    assert order == ["process", "output", "process", "output"]
    assert outputs.release_calls == 1
    assert (await _stored_state(state)).processes == {}
    await manager.close()
