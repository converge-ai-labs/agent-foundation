"""Reusable compact model-facing shell and process Toolset."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal, cast

from pydantic import Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness._tool_execution import TOOL_EXECUTION_METADATA_KEY
from a13n_harness.context import AgentContext
from a13n_harness.environment._resources import EnvironmentResources
from a13n_harness.environment.commands import CommandEnvironment, CommandLimits, CommandRequest, ShellCommand
from a13n_harness.environment.models import EnvironmentAction, EnvironmentError
from a13n_harness.environment.providers import BoundEnvironment, BoundProcessOperations
from a13n_harness.environment.retention import EnvironmentOutputCapture, EnvironmentOutputPolicy
from a13n_harness.tool_execution import _CURRENT_TOOL_EXECUTION, _OWNED_TOOL_EXECUTION
from a13n_harness.tools.metadata import (
    CanonicalResource,
    HarnessTool,
    HarnessToolMetadata,
    ToolEffect,
    ToolOutputPolicy,
    ToolResourceResolver,
)

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._results import ToolFailure, environment_failure
from .events import ShellStatusEvent
from .output import DEFAULT_TOOL_OUTPUT_CHARS, disclose_text_paths
from .process_manager import _PROCESS_OBSERVATION_ACTIONS, _OwnedProcess, _ProcessController, _project_status
from .shell_results import (
    OutputPageProjection,
    ProcessInfoResult,
    ProcessInputResult,
    ProcessObservationResult,
    ProcessSignalResult,
    ShellExecToolResult,
)

_MAX_MODEL_OUTPUT_BYTES = 1024 * 1024
_DEFAULT_INLINE_BYTES = 64 * 1024
_SHELL_INSTRUCTION = tool_instruction("environment-shell")

_NonNegativeOffset = Annotated[int, Field(ge=0)]
_PositiveTimeout = Annotated[float, Field(gt=0, allow_inf_nan=False)]
_NonNegativeTimeout = Annotated[float, Field(ge=0, allow_inf_nan=False)]
_CommandCwd = Annotated[
    str | None,
    Field(
        description=(
            "Working directory. An absolute path selects its Environment mount by advertised root and must "
            "agree with alias; a relative path resolves within the selected/default mount. "
            "Omit to use that mount's configured working directory."
        )
    ),
]
_MountAlias = Annotated[
    str | None,
    Field(
        description=(
            "Existing Environment mount name from the active context, not a command or process label. "
            "Do not invent an alias. Omit for the default mount; an absolute command cwd or a process_id "
            "selects its own mount."
        )
    ),
]


class ShellToolset:
    """Expose shell execution and independently authorized process observations."""

    def __init__(
        self,
        environment: BoundEnvironment,
        *,
        resource_resolver: Callable[[str], ToolResourceResolver] | None = None,
        execution_guard: Callable[[], None] | None = None,
    ) -> None:
        if not isinstance(environment, BoundEnvironment):
            raise TypeError("environment must be a BoundEnvironment")
        self._environment = environment
        self._resources = EnvironmentResources(environment)
        self._resource_resolver = resource_resolver
        self._execution_guard = execution_guard
        self._process_controller = _ProcessController(environment, execution_guard=self._guard_execution)

    def get_toolset(self, *, allowed_names: frozenset[str] | None = None) -> FunctionToolset[AgentContext]:
        arbitrary_command_effects: set[ToolEffect] = {
            "read",
            "write",
            "delete",
            "execute",
            "external_communication",
        }
        process_capable = any(
            _PROCESS_OBSERVATION_ACTIONS <= mount.permission_ceiling.operations
            for mount in self._environment.snapshot.mounts
        )
        shell_callable = self.shell_exec if process_capable else self.shell_exec_foreground
        actions = frozenset(
            action for mount in self._environment.snapshot.mounts for action in mount.permission_ceiling.operations
        )
        tools: list[HarnessTool] = []
        if EnvironmentAction.SHELL_EXEC in actions:
            tools.append(
                self._tool(
                    shell_callable,
                    "environment.shell_exec",
                    arbitrary_command_effects,
                    "none",
                    resources=self._command_resources,
                    name="shell_exec",
                )
            )
        if actions & {EnvironmentAction.PROCESS_LIST, EnvironmentAction.PROCESS_INSPECT}:
            tools.append(
                self._tool(
                    self.shell_info if EnvironmentAction.PROCESS_LIST in actions else self.shell_inspect,
                    "environment.process_info",
                    {"read"},
                    "read_only",
                    resources=self._info_resources,
                    name="shell_info",
                )
            )
        if {EnvironmentAction.PROCESS_WAIT, EnvironmentAction.PROCESS_READ_OUTPUT} <= actions:
            tools.append(
                self._tool(
                    self.shell_wait,
                    "environment.process_wait",
                    {"read"},
                    "read_only",
                    resources=self._process_resources,
                )
            )
        if actions & {EnvironmentAction.PROCESS_WRITE_STDIN, EnvironmentAction.PROCESS_CLOSE_STDIN}:
            tools.append(
                self._tool(
                    self.shell_input, "environment.process_input", {"write"}, "none", resources=self._process_resources
                )
            )
        if actions & {EnvironmentAction.PROCESS_SIGNAL, EnvironmentAction.PROCESS_KILL}:
            tools.append(
                self._tool(
                    self.shell_signal,
                    "environment.process_signal",
                    {"delete", "execute"},
                    "none",
                    resources=self._process_resources,
                )
            )
        if allowed_names is not None:
            tools = [tool for tool in tools if tool.name in allowed_names]
        return InstructionFunctionToolset(
            tools=tools,
            id="a13n-shell-tools",
            instructions=[_SHELL_INSTRUCTION],
        )

    async def wrap_run(
        self,
        ctx: RunContext[AgentContext],
        *,
        handler: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Bind completion wake hints only while the outer native run is active."""
        async with self._process_controller.active_run(ctx):
            return await handler()

    async def close(self) -> None:
        await self._process_controller.close()

    def resolve_process_resource(self, process_id: str) -> str:
        return self._process_controller.resource_id(process_id)

    async def shell_exec_foreground(
        self,
        ctx: RunContext[AgentContext],
        command: str,
        *,
        cwd: _CommandCwd = None,
        environment: Mapping[str, str] | None = None,
        execution_timeout_seconds: _PositiveTimeout | None = None,
        alias: _MountAlias = None,
    ) -> ShellExecToolResult:
        """Execute one command to completion and return captured output."""
        try:
            self._guard_execution()
            request = self._command_request(
                command,
                cwd=cwd,
                environment=environment,
                execution_timeout_seconds=execution_timeout_seconds,
                keep_stdin_open=False,
            )
            return await self._execute_foreground_request(
                ctx,
                request,
                alias=alias,
                expected_mount_id=None,
            )
        except EnvironmentError as exc:
            return cast(ShellExecToolResult, _environment_error_result(exc))

    async def _execute_foreground_request(
        self,
        ctx: RunContext[AgentContext],
        request: CommandRequest,
        *,
        alias: str | None,
        expected_mount_id: str | None,
    ) -> ShellExecToolResult:
        result = await self._environment.shell.exec_captured(
            request,
            alias=alias,
            expected_mount_id=expected_mount_id,
        )
        await ctx.emit(
            ShellStatusEvent(
                process_id=None,
                phase=result.status.phase,
                exit_code=result.status.exit_code,
            )
        )
        projected = cast(
            dict[str, JsonValue],
            {
                "ok": True,
                "status": _project_status(result.status),
                "stdin_open": False,
                "stdout": _project_capture(result.output.stdout),
                "stderr": _project_capture(result.output.stderr),
            },
        )
        return cast(
            ShellExecToolResult,
            await disclose_text_paths(
                ctx.deps,
                projected,
                text_paths=(("stdout", "text"), ("stderr", "text")),
                content_complete=(result.output.stdout.content_complete and result.output.stderr.content_complete),
                noun="foreground command result",
                limit=DEFAULT_TOOL_OUTPUT_CHARS,
                preserve_tail=True,
            ),
        )

    async def shell_exec(
        self,
        ctx: RunContext[AgentContext],
        command: str,
        *,
        cwd: _CommandCwd = None,
        environment: Mapping[str, str] | None = None,
        execution_timeout_seconds: _PositiveTimeout | None = None,
        alias: _MountAlias = None,
    ) -> ShellExecToolResult:
        """Run a command under the Host execution policy, with live process controls where supported."""
        try:
            request = self._command_request(
                command,
                cwd=cwd,
                environment=environment,
                execution_timeout_seconds=execution_timeout_seconds,
                keep_stdin_open=True,
            )
            mount_id, process_capable = self._environment._select_command_actions(
                request,
                alias=alias,
                actions=_PROCESS_OBSERVATION_ACTIONS,
            )
            _, input_capable = self._environment._select_command_actions(
                request,
                alias=alias,
                actions=frozenset({EnvironmentAction.PROCESS_WRITE_STDIN, EnvironmentAction.PROCESS_CLOSE_STDIN}),
            )
            request = request.model_copy(update={"keep_stdin_open": input_capable})
            if not process_capable:
                self._guard_execution()
                return await self._execute_foreground_request(
                    ctx,
                    request.model_copy(update={"keep_stdin_open": False}),
                    alias=alias,
                    expected_mount_id=mount_id,
                )
            owned = _CURRENT_TOOL_EXECUTION.get()
            if owned is not None and owned.tool_call_id == ctx.tool_call_id and ctx.deps.tool_execution is not None:
                result = await self._process_controller.run_owned(
                    request,
                    alias=alias,
                    expected_mount_id=mount_id,
                    runtime=ctx.deps.tool_execution,
                    execution_id=owned.execution_id,
                )
            else:
                result = await self._process_controller.start(
                    request,
                    alias=alias,
                    yield_time_seconds=ctx.deps._tool_executions.config.foreground_wait_seconds,
                    expected_mount_id=mount_id,
                )
                if ctx.tool_call_id is not None:
                    ctx.deps._tool_executions.returns[ctx.tool_call_id] = result
            return cast(
                ShellExecToolResult,
                await disclose_text_paths(
                    ctx.deps,
                    cast(dict[str, JsonValue], result),
                    text_paths=(("stdout", "text"), ("stderr", "text")),
                    content_complete=result["stdout"]["content_complete"] and result["stderr"]["content_complete"],
                    noun="command result",
                    limit=DEFAULT_TOOL_OUTPUT_CHARS,
                    preserve_tail=True,
                ),
            )
        except EnvironmentError as exc:
            return cast(ShellExecToolResult, _environment_error_result(exc))

    async def shell_info(
        self,
        ctx: RunContext[AgentContext],
        process_id: str | None = None,
        *,
        alias: _MountAlias = None,
        limit: Annotated[int, Field(ge=1, le=1000)] = 50,
    ) -> ProcessInfoResult:
        """Discover native processes, or inspect a local process or live Shell execution reference."""
        try:
            if process_id is None:
                return await self._process_controller.info(None, alias=alias, limit=limit)
            async with self._process_access(
                ctx.deps, process_id, actions=frozenset({EnvironmentAction.PROCESS_INSPECT}), alias=alias
            ) as (controller, reference, processes):
                result = await controller.info(
                    reference, alias=None if process_id.startswith("exec-") else alias, limit=limit, processes=processes
                )
                assert "process_id" in result
                result["process_id"] = process_id
                return result
        except EnvironmentError as exc:
            return cast(ProcessInfoResult, _environment_error_result(exc))

    async def shell_inspect(
        self,
        ctx: RunContext[AgentContext],
        process_id: str,
        *,
        alias: _MountAlias = None,
    ) -> ProcessInfoResult:
        """Inspect a local process or live Shell execution reference; native listing is unavailable."""
        return await self.shell_info(ctx, process_id, alias=alias)

    async def shell_wait(
        self,
        ctx: RunContext[AgentContext],
        process_id: str,
        *,
        stdout_offset: _NonNegativeOffset = 0,
        stderr_offset: _NonNegativeOffset = 0,
        timeout_seconds: _NonNegativeTimeout = 180,
    ) -> ProcessObservationResult:
        """Wait boundedly, or poll with zero, then read available output at explicit offsets."""
        try:
            actions = {EnvironmentAction.PROCESS_INSPECT, EnvironmentAction.PROCESS_READ_OUTPUT}
            if timeout_seconds > 0:
                actions.add(EnvironmentAction.PROCESS_WAIT)
            async with self._process_access(
                ctx.deps, process_id, actions=frozenset(actions), timeout_seconds=timeout_seconds
            ) as (controller, reference, processes):
                try:
                    result = await controller.wait(
                        reference,
                        stdout_offset=stdout_offset,
                        stderr_offset=stderr_offset,
                        timeout_seconds=timeout_seconds,
                        processes=processes,
                    )
                except asyncio.CancelledError:
                    if _OWNED_TOOL_EXECUTION.get():
                        raise
                    result = await controller.wait(
                        reference,
                        stdout_offset=stdout_offset,
                        stderr_offset=stderr_offset,
                        timeout_seconds=0,
                        processes=processes,
                    )
                result["process_id"] = process_id
                if ctx.tool_call_id is not None:
                    ctx.deps._tool_executions.returns[ctx.tool_call_id] = result
            return cast(
                ProcessObservationResult,
                await disclose_text_paths(
                    ctx.deps,
                    cast(dict[str, JsonValue], result),
                    text_paths=(("stdout", "text"), ("stderr", "text")),
                    content_complete=result["stdout"]["content_complete"] and result["stderr"]["content_complete"],
                    noun="process observation",
                    limit=DEFAULT_TOOL_OUTPUT_CHARS,
                    preserve_tail=True,
                ),
            )
        except EnvironmentError as exc:
            return cast(ProcessObservationResult, _environment_error_result(exc))

    async def shell_input(
        self,
        ctx: RunContext[AgentContext],
        process_id: str,
        data: str = "",
        *,
        close_stdin: bool = False,
    ) -> ProcessInputResult:
        """Write UTF-8 stdin and optionally close stdin without reading output."""
        try:
            actions = {EnvironmentAction.PROCESS_INSPECT}
            if data:
                actions.add(EnvironmentAction.PROCESS_WRITE_STDIN)
            if close_stdin:
                actions.add(EnvironmentAction.PROCESS_CLOSE_STDIN)
            async with self._process_access(ctx.deps, process_id, actions=frozenset(actions)) as (
                controller,
                reference,
                processes,
            ):
                result = await controller.write_input(reference, data, close_stdin=close_stdin, processes=processes)
                result["process_id"] = process_id
                return result
        except EnvironmentError as exc:
            return cast(ProcessInputResult, _environment_error_result(exc))

    async def shell_signal(
        self,
        ctx: RunContext[AgentContext],
        process_id: str,
        signal: Literal["interrupt", "terminate", "kill"],
    ) -> ProcessSignalResult:
        """Control a live process without reading output."""
        try:
            action = EnvironmentAction.PROCESS_KILL if signal == "kill" else EnvironmentAction.PROCESS_SIGNAL
            async with self._process_access(ctx.deps, process_id, actions=frozenset({action})) as (
                controller,
                reference,
                processes,
            ):
                result = await controller.signal(reference, signal, processes=processes)
                result["process_id"] = process_id
                return result
        except EnvironmentError as exc:
            return cast(ProcessSignalResult, _environment_error_result(exc))

    async def _command_resources(
        self, arguments: Mapping[str, object], *, context: AgentContext
    ) -> tuple[CanonicalResource, ...]:
        return (
            await self._resources.binding(
                _optional_string_argument(arguments, "alias"), "shell", path=_optional_string_argument(arguments, "cwd")
            ),
        )

    async def _info_resources(
        self, arguments: Mapping[str, object], *, context: AgentContext
    ) -> tuple[CanonicalResource, ...]:
        if arguments.get("process_id") is not None:
            return await self._process_resources(arguments, context=context)
        return (await self._resources.binding(_optional_string_argument(arguments, "alias"), "processes"),)

    async def _process_resources(
        self, arguments: Mapping[str, object], *, context: AgentContext
    ) -> tuple[CanonicalResource, ...]:
        process_id = _string_argument(arguments, "process_id")
        owned = self._runtime_process(context, process_id)
        return (
            CanonicalResource(
                namespace="environment",
                kind="managed-process",
                identifier=owned.resource_id if owned is not None else self.resolve_process_resource(process_id),
            ),
        )

    def _runtime_process(self, context: AgentContext, process_id: str) -> _OwnedProcess | None:
        if not process_id.startswith("exec-"):
            return None
        runtime = context.tool_execution
        resource = (
            runtime.resource(context.thread_id, process_id)
            if runtime is not None and context._tool_executions.enabled
            else None
        )
        if not isinstance(resource, _OwnedProcess):
            raise EnvironmentError(
                "No live Shell control surface is available. Use tool_execution_info or tool_execution_result for this execution.",
                code="environment_reference_invalid",
            )
        return resource

    @asynccontextmanager
    async def _process_access(
        self,
        context: AgentContext,
        process_id: str,
        *,
        actions: frozenset[EnvironmentAction],
        alias: str | None = None,
        timeout_seconds: float = 0,
    ) -> AsyncGenerator[tuple[_ProcessController, str, BoundProcessOperations]]:
        self._guard_execution()
        owned = self._runtime_process(context, process_id)
        if owned is None:
            yield self._process_controller, process_id, self._environment.processes
        else:
            async with owned.access(
                self._environment, actions=actions, alias=alias, timeout_seconds=timeout_seconds
            ) as selected:
                yield selected

    def _tool(
        self,
        function: Callable[..., object],
        tool_id: str,
        effects: set[ToolEffect],
        idempotency: Literal["none", "read_only"],
        *,
        resources: ToolResourceResolver,
        name: str | None = None,
    ) -> HarnessTool:
        return HarnessTool(
            function,
            name=name,
            metadata={TOOL_EXECUTION_METADATA_KEY: "owned" if tool_id == "environment.shell_exec" else "control"},
            harness_metadata=HarnessToolMetadata(
                tool_id=tool_id,
                effects=frozenset(effects),
                credential_audiences=(),
                idempotency=idempotency,
                output_policy=ToolOutputPolicy(
                    max_inline_bytes=256 * 1024,
                    max_output_bytes=4 * 1024 * 1024,
                    overflow="truncate",
                    redact=True,
                ),
                resource_resolver=(
                    self._resource_resolver(tool_id)
                    if self._resource_resolver is not None
                    else self._resources.resolver(resources)
                ),
            ),
        )

    @staticmethod
    def _command_request(
        command: str,
        *,
        cwd: str | None,
        environment: Mapping[str, str] | None,
        execution_timeout_seconds: float | None,
        keep_stdin_open: bool,
    ) -> CommandRequest:
        try:
            return CommandRequest(
                command=ShellCommand(profile_id="default", script=command),
                cwd=cwd,
                environment=CommandEnvironment(set=dict(environment or {})),
                limits=CommandLimits(wall_time_seconds=execution_timeout_seconds),
                keep_stdin_open=keep_stdin_open,
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=_DEFAULT_INLINE_BYTES,
                    max_output_bytes=_MAX_MODEL_OUTPUT_BYTES,
                    overflow="truncate",
                ),
            )
        except (TypeError, ValueError) as exc:
            raise EnvironmentError(
                "Environment command request is invalid.",
                code="environment_request_invalid",
            ) from exc

    def _guard_execution(self) -> None:
        if self._execution_guard is not None:
            self._execution_guard()


def _project_capture(capture: EnvironmentOutputCapture) -> OutputPageProjection:
    if capture.inline is not None:
        start_offset = capture.available_start
        data = capture.inline
    elif capture.preview:
        start_offset = capture.preview[0].start_offset
        data = b"".join(segment.data for segment in capture.preview)
    else:
        start_offset = capture.available_start
        data = b""
    return {
        "requested_offset": 0,
        "start_offset": start_offset,
        "next_offset": start_offset + len(data),
        "available_start": capture.available_start,
        "available_end": capture.available_end,
        "origin": capture.origin,
        "coverage": capture.coverage,
        "observation_closed": capture.observation_closed,
        "reason": capture.reason,
        "produced_bytes": capture.produced_bytes,
        "producer_complete": capture.producer_complete,
        "content_complete": capture.content_complete,
        "omitted_before_bytes": start_offset,
        "text": data.decode("utf-8", errors="replace"),
    }


def _environment_error_result(error: EnvironmentError) -> ToolFailure:
    result = environment_failure(error)
    result["error"]["outcome_known"] = error.code != "environment_unknown_outcome"
    return result


def _string_argument(arguments: Mapping[str, object], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value:
        raise EnvironmentError(
            f"Environment tool argument {name!r} is invalid.",
            code="environment_request_invalid",
            details={"field": name, "reason": "invalid_value", "hint": "Supply a non-empty string for this field."},
        )
    return value


def _optional_string_argument(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise EnvironmentError(
            f"Environment tool argument {name!r} is invalid.",
            code="environment_request_invalid",
            details={"field": name, "reason": "invalid_value", "hint": "Supply a non-empty string for this field."},
        )
    return value


__all__ = ["ShellToolset"]
