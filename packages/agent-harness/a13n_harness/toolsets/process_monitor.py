"""Reusable monitored-process Toolset over process and monitor ports."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness.context import AgentContext
from a13n_harness.environment.commands import (
    ArgvCommand,
    BoundProcessHandle,
    CommandEnvironment,
    CommandLimits,
    CommandRequest,
    ShellCommand,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.environment.retention import EnvironmentOutputPolicy
from a13n_harness.errors import DefinitionError
from a13n_harness.tools.metadata import (
    HarnessTool,
    HarnessToolMetadata,
    ToolOutputPolicy,
)

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._results import ToolFailure
from .shell import ShellProcessProjector
from .shell_results import ProcessProjection

if TYPE_CHECKING:
    from a13n_harness.capabilities.process_monitor import (
        MonitoredProcessConfiguration,
        MonitoredProcessMonitor,
    )


class MonitoredProcessSuccess(ProcessProjection):
    ok: Literal[True]
    monitored: Literal[True]


class MonitoredProcessRegistrationFailure(ToolFailure):
    process: str


type MonitoredProcessToolResult = MonitoredProcessSuccess | MonitoredProcessRegistrationFailure | ToolFailure


class MonitoredProcessToolset:
    """Standard monitored-start semantics over one process domain and monitor."""

    def __init__(
        self,
        *,
        monitor: MonitoredProcessMonitor,
        projector: ShellProcessProjector,
        environment: BoundEnvironment,
        configuration: MonitoredProcessConfiguration,
    ) -> None:
        self._monitor = monitor
        self._projector = projector
        self._environment = environment
        self._configuration = configuration.model_copy(deep=True)

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        tool = HarnessTool(
            self.environment_process_monitor,
            harness_metadata=HarnessToolMetadata(
                tool_id="environment.process_monitor",
                effects=frozenset({"read", "write", "delete", "execute", "external_communication"}),
                credential_audiences=(),
                idempotency="none",
                output_policy=ToolOutputPolicy(
                    max_inline_bytes=64 * 1024,
                    max_output_bytes=256 * 1024,
                    overflow="truncate",
                    redact=True,
                ),
                resource_resolver=self._projector.process_start_resource_resolver(),
            ),
        )
        return InstructionFunctionToolset(
            tools=[tool],
            id="a13n-monitored-process-tools",
            instructions=tool_instruction("process_monitor"),
        )

    async def environment_process_monitor(
        self,
        ctx: RunContext[AgentContext],
        command: ArgvCommand | ShellCommand,
        *,
        alias: str | None = None,
        cwd: str | None = None,
        environment: Mapping[str, str] | None = None,
        unset_environment: Sequence[str] = (),
        network: Literal["configured", "deny"] = "configured",
        wall_time_seconds: Annotated[float | None, Field(default=None, gt=0, allow_inf_nan=False)] = None,
        initial_stdin: str | None = None,
        keep_stdin_open: bool = False,
    ) -> MonitoredProcessToolResult:
        try:
            monitor, projector = self._monitor, self._projector
            request = CommandRequest(
                command=command,
                cwd=cwd,
                environment=CommandEnvironment(set=dict(environment or {}), unset=tuple(unset_environment)),
                network=network,
                limits=CommandLimits(wall_time_seconds=wall_time_seconds),
                initial_stdin=initial_stdin.encode("utf-8") if initial_stdin is not None else None,
                keep_stdin_open=keep_stdin_open,
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=self._configuration.max_inline_bytes,
                    max_output_bytes=self._configuration.max_output_bytes,
                    overflow="retain",
                ),
            )
            process, projected = await projector.start_process(ctx, request, alias=alias)
            reference: str | None = None
            try:
                projected_reference = projected["process"]
                if not isinstance(projected_reference, str):
                    raise DefinitionError(
                        "Environment process projector returned an invalid compact reference.",
                        code="dynamic_environment_invalid",
                    )
                reference = projected_reference
                await monitor.register(
                    process=process.handle,
                    reference=reference,
                    environment=self._environment,
                )
            except asyncio.CancelledError as cancellation:
                cleanup = asyncio.create_task(_terminate_started_process(self._environment, process.handle))
                cleanup_error, repeated_cancellation = await _await_owned_cleanup(cleanup)
                if cleanup_error is not None:
                    cancellation.add_note(f"Monitored process cancellation cleanup failed: {cleanup_error!r}")
                if repeated_cancellation is not None:
                    cancellation.add_note("Cancellation was requested again while process cleanup was finishing.")
                raise cancellation
            except Exception as registration_error:
                cleanup = asyncio.create_task(_terminate_started_process(self._environment, process.handle))
                cleanup_error, cancellation = await _await_owned_cleanup(cleanup)
                if cancellation is not None:
                    cancellation.add_note(f"Process registration had already failed: {registration_error!r}")
                    if cleanup_error is not None:
                        cancellation.add_note(f"Monitored process cleanup also failed: {cleanup_error!r}")
                    raise cancellation from registration_error
                failure: ToolFailure = {
                    "ok": False,
                    "error": {
                        "code": "monitored_process_registration_failed",
                        "retry_hint": "dependency_change",
                        "outcome_known": cleanup_error is None,
                    },
                }
                if reference is None:
                    return failure
                return {**failure, "process": reference}
            return {"ok": True, **projected, "monitored": True}
        except EnvironmentError as exc:
            return {"ok": False, "error": {"code": exc.code, "retry_hint": exc.retry_hint}}


async def _terminate_started_process(
    environment: BoundEnvironment,
    process: BoundProcessHandle,
) -> None:
    await environment.processes.kill(process)
    await environment.processes.wait(
        process,
        condition="tree_cleaned",
        timeout_seconds=5.0,
    )


async def _await_owned_cleanup(
    task: asyncio.Task[None],
) -> tuple[BaseException | None, asyncio.CancelledError | None]:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if cancellation is None:
                cancellation = exc
        except BaseException:
            break
    if task.cancelled():
        return asyncio.CancelledError("Process cleanup task was cancelled."), cancellation
    return task.exception(), cancellation


__all__ = ["MonitoredProcessToolResult", "MonitoredProcessToolset"]
