from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from a13n_environment_provider import (
    DirectLocalProviderConfiguration,
    DirectLocalRootConfiguration,
)
from a13n_harness import (
    ArgvCommand,
    CommandLimits,
    CommandRequest,
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
    EnvironmentAction,
    EnvironmentBindingRequest,
    EnvironmentOutputPolicy,
    EnvironmentPermissionSet,
    EnvironmentStateLimits,
    EnvironmentTopologyLimits,
    EnvironmentTopologyRequest,
    HarnessBuilder,
    InProcessMonitoredProcessMonitor,
    MonitoredProcessCapability,
    MonitoredProcessNotification,
    MonitoredProcessRunCapability,
    RunBindings,
    create_environment_run_binding,
)
from a13n_harness.environment.local.binding import DirectLocalEnvironmentProviderBinding
from a13n_harness.tools import InvocationPolicyCapability, InvocationPolicyDecision
from pydantic_ai.agent.spec import AgentSpec
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel

pytestmark = pytest.mark.anyio
requires_posix_process_groups = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Direct Local process groups require POSIX",
)
_PROCESS_EXECUTABLE = Path(sys.executable).resolve()


def _configuration(*, max_reference_entries: int = 64) -> DynamicEnvironmentConfiguration:
    return DynamicEnvironmentConfiguration(
        max_reference_entries=max_reference_entries,
    )


def _local_binding(root: Path):
    provider = DirectLocalEnvironmentProviderBinding(
        DirectLocalProviderConfiguration(
            environment_id="monitored-process-test",
            root=DirectLocalRootConfiguration(path=root),
            allowed_executables=frozenset({_PROCESS_EXECUTABLE}),
        )
    )
    return create_environment_run_binding(
        initial_topology=EnvironmentTopologyRequest(
            topology_version=1,
            bindings=(
                EnvironmentBindingRequest(
                    binding_id="binding-1",
                    binding_revision=1,
                    alias="local",
                    permission_ceiling=EnvironmentPermissionSet(operations=frozenset(EnvironmentAction)),
                    default_working_directory="/",
                    provider_binding=provider,
                ),
            ),
            default_binding_id="binding-1",
        ),
        topology_limits=EnvironmentTopologyLimits(),
        state_limits=EnvironmentStateLimits(),
    )


class _Allow:
    async def __call__(self, invocation, metadata, *, context):
        del invocation, metadata, context
        return InvocationPolicyDecision.allow()


class _BlockingMonitor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.process: Any = None
        self.environment: Any = None
        self.closed = False
        self.phase_at_close: str | None = None

    async def register(self, *, process, reference, environment) -> None:
        del reference
        self.process = process
        self.environment = environment
        self.started.set()
        await asyncio.Future()

    async def pending(self):
        return ()

    async def acknowledge(self, notification) -> None:
        del notification

    async def close(self) -> None:
        self.closed = True
        if self.process is not None:
            info = await self.environment.processes.inspect(self.process)
            self.phase_at_close = info.status.phase


class _ImmediateMonitor:
    def __init__(self) -> None:
        self.notifications: list[MonitoredProcessNotification] = []
        self.registered: list[str] = []
        self.acknowledged: list[str] = []
        self.closed = False
        self.environment: Any = None

    async def register(self, *, process, reference, environment) -> None:
        del process
        self.environment = environment
        self.registered.append(reference)
        self.notifications.append(
            MonitoredProcessNotification(
                notification_id="notification-1",
                kind="output",
                process=reference,
                phase="running",
                produced_bytes=1,
            )
        )

    async def pending(self):
        return tuple(self.notifications)

    async def acknowledge(self, notification) -> None:
        if notification in self.notifications:
            self.notifications.remove(notification)
            self.acknowledged.append(notification.notification_id)

    async def close(self) -> None:
        # Cleanup must run before the Environment lifecycle exits.
        assert self.environment is not None
        assert self.environment.topology.topology_version == 1
        self.closed = True


@requires_posix_process_groups
async def test_monitored_process_shares_process_reference_status_and_accepted_delivery(tmp_path: Path) -> None:
    monitor = _ImmediateMonitor()
    calls: list[list[ModelMessage]] = []
    process_reference: str | None = None

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        nonlocal process_reference
        calls.append(messages)
        returns = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict)
        ]
        if not returns:
            assert "environment_process_monitor" in {tool.name for tool in info.function_tools}
            yield {
                0: DeltaToolCall(
                    name="environment_process_monitor",
                    json_args=json.dumps(
                        {
                            "command": {
                                "kind": "argv",
                                "executable": str(_PROCESS_EXECUTABLE),
                                "arguments": ["-c", "print('ready')"],
                            }
                        }
                    ),
                    tool_call_id="monitor-1",
                )
            }
        elif len(returns) == 1:
            process_reference = returns[0]["process"]
            assert process_reference == "process-1"
            assert "monitored-process-notifications" in _user_text(messages)
            assert "process-1 has new buffered output" in _user_text(messages)
            yield {
                0: DeltaToolCall(
                    name="environment_process_status",
                    json_args="{}",
                    tool_call_id="status-1",
                )
            }
        else:
            status_result = returns[-1]
            statuses = status_result["processes"]
            assert statuses[0]["process"] == process_reference
            assert "stdout" not in statuses[0]
            assert "produced_bytes" in statuses[0]
            assert status_result["next_cursor"] is None
            assert status_result["truncated"] is False
            yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(
            DynamicEnvironmentCapability(_configuration()),
            MonitoredProcessCapability(),
        ),
    )
    result = await executable.run(
        "start",
        bindings=RunBindings.local(
            environment=_local_binding(tmp_path),
            capabilities=(
                InvocationPolicyCapability(evaluator=_Allow(), max_dispatch_retries=0),
                MonitoredProcessRunCapability(monitor=monitor),
            ),
        ),
    )

    assert result.output_or_raise() == "done"
    assert process_reference == "process-1"
    assert monitor.registered == ["process-1"]
    assert monitor.acknowledged == ["notification-1"]
    assert monitor.closed is True
    assert len(calls) == 3


async def test_monitored_process_requires_fresh_host_attachment_before_model_request() -> None:
    model_called = False

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal model_called
        del messages, info
        model_called = True
        yield "done"

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()), MonitoredProcessCapability()),
    )
    with pytest.raises(Exception) as exc_info:
        await executable.run("start", bindings=RunBindings.local())

    assert getattr(exc_info.value, "code", None) == "monitored_process_binding_missing"
    assert model_called is False


@requires_posix_process_groups
async def test_in_process_monitor_detects_fast_completion_without_losing_record(tmp_path: Path) -> None:
    binding = _local_binding(tmp_path)
    run_bindings = RunBindings.local(environment=binding)
    ready = asyncio.Event()
    observed: list[MonitoredProcessNotification] = []

    def on_ready(notification: MonitoredProcessNotification) -> None:
        observed.append(notification)
        if notification.kind == "completion":
            ready.set()

    monitor = InProcessMonitoredProcessMonitor(
        poll_interval_seconds=0.01,
        on_ready=on_ready,
    )
    async with binding.bind(run_id="run-monitor", instance=run_bindings.instance) as environment:
        await environment.activate()
        started = await environment.processes.start(
            CommandRequest(
                command=ArgvCommand(
                    executable=str(_PROCESS_EXECUTABLE),
                    arguments=("-c", "print('done')"),
                ),
                limits=CommandLimits(wall_time_seconds=5),
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=1024,
                    max_output_bytes=4096,
                    overflow="retain",
                ),
            )
        )
        await monitor.register(
            process=started.process.handle,
            reference="process-1",
            environment=environment,
        )
        await asyncio.wait_for(ready.wait(), timeout=2)
        pending = await monitor.pending()
        assert len(pending) == 1
        assert pending[0].kind == "completion"
        assert pending[0].process == "process-1"
        await monitor.close()

    # The bounded record remains available for Host transfer after live observation closes.
    pending_after_close = await monitor.pending()
    assert len(pending_after_close) == 1
    assert pending_after_close[0].kind == "completion"
    assert pending_after_close[0].process is None
    assert "previous logical run" in (pending_after_close[0].message or "")
    assert any(item.kind == "completion" for item in observed)


@requires_posix_process_groups
async def test_in_process_monitor_retires_acknowledged_terminal_records(tmp_path: Path) -> None:
    binding = _local_binding(tmp_path)
    run_bindings = RunBindings.local(environment=binding)
    monitor = InProcessMonitoredProcessMonitor(
        poll_interval_seconds=0.01,
        max_pending=1,
        max_monitored=1,
    )

    async with binding.bind(run_id="run-monitor-retire", instance=run_bindings.instance) as environment:
        await environment.activate()
        for sequence in (1, 2):
            started = await environment.processes.start(
                CommandRequest(
                    command=ArgvCommand(
                        executable=str(_PROCESS_EXECUTABLE),
                        arguments=("-c", f"print({sequence})"),
                    ),
                    limits=CommandLimits(wall_time_seconds=5),
                    output_policy=EnvironmentOutputPolicy(
                        max_inline_bytes=1024,
                        max_output_bytes=4096,
                        overflow="retain",
                    ),
                )
            )
            await monitor.register(
                process=started.process.handle,
                reference=f"process-{sequence}",
                environment=environment,
            )
            for _ in range(200):
                pending = await monitor.pending()
                if pending and pending[0].kind == "completion":
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("completion was not retained")
            await monitor.acknowledge(pending[0])
            assert await monitor.pending() == ()

        await monitor.close()

    assert await monitor.pending() == ()


@requires_posix_process_groups
async def test_in_process_monitor_backpressures_before_pending_completion_can_be_lost(tmp_path: Path) -> None:
    binding = _local_binding(tmp_path)
    run_bindings = RunBindings.local(environment=binding)
    monitor = InProcessMonitoredProcessMonitor(
        poll_interval_seconds=0.01,
        max_pending=1,
        max_monitored=2,
    )

    async with binding.bind(run_id="run-monitor-capacity", instance=run_bindings.instance) as environment:
        await environment.activate()
        first = await environment.processes.start(
            CommandRequest(
                command=ArgvCommand(
                    executable=str(_PROCESS_EXECUTABLE),
                    arguments=("-c", "print('first')"),
                ),
                limits=CommandLimits(wall_time_seconds=5),
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=1024,
                    max_output_bytes=4096,
                    overflow="retain",
                ),
            )
        )
        second = await environment.processes.start(
            CommandRequest(
                command=ArgvCommand(
                    executable=str(_PROCESS_EXECUTABLE),
                    arguments=("-c", "print('second')"),
                ),
                limits=CommandLimits(wall_time_seconds=5),
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=1024,
                    max_output_bytes=4096,
                    overflow="retain",
                ),
            )
        )
        await monitor.register(
            process=first.process.handle,
            reference="process-1",
            environment=environment,
        )
        with pytest.raises(RuntimeError, match="cannot retain"):
            await monitor.register(
                process=second.process.handle,
                reference="process-2",
                environment=environment,
            )
        for _ in range(200):
            pending = await monitor.pending()
            if pending and pending[0].kind == "completion":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("first completion was not retained")

        assert len(pending) == 1
        assert pending[0].process == "process-1"
        await monitor.close()


async def test_monitored_process_rejects_orphan_run_attachment_before_model_request() -> None:
    model_called = False

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal model_called
        del messages, info
        model_called = True
        yield "done"

    monitor = _ImmediateMonitor()
    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
    )

    with pytest.raises(Exception) as exc_info:
        await executable.run(
            "start",
            bindings=RunBindings.local(capabilities=(MonitoredProcessRunCapability(monitor=monitor),)),
        )

    assert getattr(exc_info.value, "code", None) == "monitored_process_owner_missing"
    assert model_called is False


@requires_posix_process_groups
async def test_in_process_monitor_close_publishes_gap_for_running_process(tmp_path: Path) -> None:
    binding = _local_binding(tmp_path)
    run_bindings = RunBindings.local(environment=binding)
    monitor = InProcessMonitoredProcessMonitor(poll_interval_seconds=1)

    async with binding.bind(run_id="run-monitor-gap", instance=run_bindings.instance) as environment:
        await environment.activate()
        started = await environment.processes.start(
            CommandRequest(
                command=ArgvCommand(
                    executable=str(_PROCESS_EXECUTABLE),
                    arguments=("-c", "import time; time.sleep(10)"),
                ),
                limits=CommandLimits(wall_time_seconds=20),
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=1024,
                    max_output_bytes=4096,
                    overflow="retain",
                ),
            )
        )
        await monitor.register(
            process=started.process.handle,
            reference="process-1",
            environment=environment,
        )
        await monitor.close()
        pending = await monitor.pending()

        assert len(pending) == 1
        assert pending[0].kind == "gap"
        assert pending[0].process is None
        assert pending[0].phase in {"starting", "running"}
        assert "previous logical run" in (pending[0].message or "")


@requires_posix_process_groups
async def test_in_process_monitor_surfaces_observer_failure_as_pending_gap() -> None:
    class _Processes:
        def __init__(self) -> None:
            self.calls = 0

        async def inspect(self, process):
            del process
            self.calls += 1
            if self.calls == 1:
                return initial
            raise RuntimeError("observer failed")

    class _Environment:
        processes = _Processes()

    binding = _local_binding(Path.cwd())
    run_bindings = RunBindings.local(environment=binding)
    async with binding.bind(run_id="run-monitor-failure", instance=run_bindings.instance) as environment:
        await environment.activate()
        started = await environment.processes.start(
            CommandRequest(
                command=ArgvCommand(
                    executable=str(_PROCESS_EXECUTABLE),
                    arguments=("-c", "import time; time.sleep(10)"),
                ),
                limits=CommandLimits(wall_time_seconds=20),
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=1024,
                    max_output_bytes=4096,
                    overflow="retain",
                ),
            )
        )
        initial = started.process
        monitor = InProcessMonitoredProcessMonitor(poll_interval_seconds=0.01)
        await monitor.register(
            process=initial.handle,
            reference="process-1",
            environment=_Environment(),  # type: ignore[arg-type]
        )
        for _ in range(100):
            pending = await monitor.pending()
            if pending and pending[0].kind == "gap":
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("observer failure was not published")

        assert "RuntimeError" in (pending[0].message or "")
        await monitor.close()


@requires_posix_process_groups
async def test_process_monitor_cancellation_finishes_kill_before_reraising(tmp_path: Path) -> None:
    monitor = _BlockingMonitor()

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        del messages, info
        yield {
            0: DeltaToolCall(
                name="environment_process_monitor",
                json_args=json.dumps(
                    {
                        "command": {
                            "kind": "argv",
                            "executable": str(_PROCESS_EXECUTABLE),
                            "arguments": ["-c", "import time; time.sleep(10)"],
                        }
                    }
                ),
                tool_call_id="monitor-cancel-1",
            )
        }

    executable = HarnessBuilder().build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=stream),
        capabilities=(DynamicEnvironmentCapability(_configuration()), MonitoredProcessCapability()),
    )
    run_task = asyncio.create_task(
        executable.run(
            "start",
            bindings=RunBindings.local(
                environment=_local_binding(tmp_path),
                capabilities=(
                    InvocationPolicyCapability(evaluator=_Allow(), max_dispatch_retries=0),
                    MonitoredProcessRunCapability(monitor=monitor),
                ),
            ),
        )
    )
    await asyncio.wait_for(monitor.started.wait(), timeout=2)
    run_task.cancel()
    run_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await run_task

    assert monitor.closed is True
    assert monitor.phase_at_close in {"exited", "signaled", "cancelled", "failed"}


def _user_text(messages: list[ModelMessage]) -> str:
    return "\n".join(
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    )
