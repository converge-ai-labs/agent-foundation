"""Reusable compact model-facing shell and process Toolset."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any, Literal, cast

from pydantic import Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness.context import AgentContext
from a13n_harness.environment.commands import (
    CommandEnvironment,
    CommandLimits,
    CommandRequest,
    ShellCommand,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.environment.retention import EnvironmentOutputCapture, EnvironmentOutputPolicy
from a13n_harness.tools.metadata import (
    HarnessTool,
    HarnessToolMetadata,
    ToolEffect,
    ToolOutputPolicy,
    ToolResourceResolver,
)

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._results import ToolError, ToolFailure
from .output import DEFAULT_TOOL_OUTPUT_CHARS, disclose_text_paths
from .process_manager import _RUN_PROCESS_ACTIONS, _project_status, _RunProcessController
from .shell_results import (
    OutputPageProjection,
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


class ShellToolset:
    """Expose foreground shell or the four-tool Run-owned process surface."""

    def __init__(
        self,
        environment: BoundEnvironment,
        *,
        process_capable: bool,
        resource_resolver: Callable[[str], ToolResourceResolver] | None = None,
        execution_guard: Callable[[], None] | None = None,
    ) -> None:
        if not isinstance(environment, BoundEnvironment):
            raise TypeError("environment must be a BoundEnvironment")
        if type(process_capable) is not bool:
            raise TypeError("process_capable must be a boolean")
        self._environment = environment
        self._process_capable = process_capable
        self._resource_resolver = resource_resolver
        self._execution_guard = execution_guard
        self._process_controller = (
            _RunProcessController(environment, execution_guard=execution_guard) if process_capable else None
        )

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        arbitrary_command_effects: set[ToolEffect] = {
            "read",
            "write",
            "delete",
            "execute",
            "external_communication",
        }
        shell_callable = self.shell_exec if self._process_capable else self.shell_exec_foreground
        tools: list[HarnessTool] = [
            self._tool(
                shell_callable,
                "environment.shell_exec",
                arbitrary_command_effects,
                "none",
                name="shell_exec",
            )
        ]
        if self._process_capable:
            tools.extend(
                (
                    self._tool(self.shell_wait, "environment.process_wait", {"read"}, "read_only"),
                    self._tool(self.shell_input, "environment.process_input", {"write"}, "none"),
                    self._tool(
                        self.shell_signal,
                        "environment.process_signal",
                        {"delete", "execute"},
                        "none",
                    ),
                )
            )
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
        controller = self._process_controller
        if controller is None:
            return await handler()
        async with controller.active_run(ctx):
            return await handler()

    async def close(self) -> None:
        controller = self._process_controller
        if controller is not None:
            await controller.close()

    def resolve_process_resource(self, process_id: str) -> str:
        return self._require_process_controller().resource_id(process_id)

    async def shell_exec_foreground(
        self,
        ctx: RunContext[AgentContext],
        command: str,
        *,
        cwd: str | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: _PositiveTimeout | None = None,
        alias: str | None = None,
    ) -> ShellExecToolResult:
        """Execute one command to completion and return captured output."""
        try:
            self._guard_execution()
            request = self._command_request(
                command,
                cwd=cwd,
                environment=environment,
                timeout_seconds=timeout_seconds,
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
        cwd: str | None = None,
        environment: Mapping[str, str] | None = None,
        yield_time_seconds: _NonNegativeTimeout = 10,
        timeout_seconds: _PositiveTimeout | None = None,
        alias: str | None = None,
    ) -> ShellExecToolResult:
        """Start a command and automatically yield a Run-owned process when still live."""
        try:
            request = self._command_request(
                command,
                cwd=cwd,
                environment=environment,
                timeout_seconds=timeout_seconds,
                keep_stdin_open=True,
            )
            mount_id, process_capable = self._environment._select_command_actions(
                request,
                alias=alias,
                actions=_RUN_PROCESS_ACTIONS,
            )
            if not process_capable:
                self._guard_execution()
                return await self._execute_foreground_request(
                    ctx,
                    request.model_copy(update={"keep_stdin_open": False}),
                    alias=alias,
                    expected_mount_id=mount_id,
                )
            result = await self._require_process_controller().start(
                request,
                alias=alias,
                yield_time_seconds=yield_time_seconds,
                expected_mount_id=mount_id,
            )
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

    async def shell_wait(
        self,
        ctx: RunContext[AgentContext],
        process_id: str,
        *,
        stdout_offset: _NonNegativeOffset = 0,
        stderr_offset: _NonNegativeOffset = 0,
        timeout_seconds: _NonNegativeTimeout = 180,
    ) -> ProcessObservationResult:
        """Wait boundedly, or poll with zero, then read retained output at explicit offsets."""
        try:
            result = await self._require_process_controller().wait(
                process_id,
                stdout_offset=stdout_offset,
                stderr_offset=stderr_offset,
                timeout_seconds=timeout_seconds,
            )
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
        del ctx
        try:
            return await self._require_process_controller().write_input(
                process_id,
                data,
                close_stdin=close_stdin,
            )
        except EnvironmentError as exc:
            return cast(ProcessInputResult, _environment_error_result(exc))

    async def shell_signal(
        self,
        ctx: RunContext[AgentContext],
        process_id: str,
        signal: Literal["interrupt", "terminate", "kill"],
    ) -> ProcessSignalResult:
        """Control a live process without reading output."""
        del ctx
        try:
            return await self._require_process_controller().signal(process_id, signal)
        except EnvironmentError as exc:
            return cast(ProcessSignalResult, _environment_error_result(exc))

    def _tool(
        self,
        function: Callable[..., object],
        tool_id: str,
        effects: set[ToolEffect],
        idempotency: Literal["none", "read_only"],
        *,
        name: str | None = None,
    ) -> HarnessTool:
        return HarnessTool(
            function,
            name=name,
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
                resource_resolver=(self._resource_resolver(tool_id) if self._resource_resolver is not None else None),
                shell_review=tool_id == "environment.shell_exec",
            ),
        )

    @staticmethod
    def _command_request(
        command: str,
        *,
        cwd: str | None,
        environment: Mapping[str, str] | None,
        timeout_seconds: float | None,
        keep_stdin_open: bool,
    ) -> CommandRequest:
        try:
            return CommandRequest(
                command=ShellCommand(profile_id="default", script=command),
                cwd=cwd,
                environment=CommandEnvironment(set=dict(environment or {})),
                limits=CommandLimits(wall_time_seconds=timeout_seconds),
                keep_stdin_open=keep_stdin_open,
                output_policy=EnvironmentOutputPolicy(
                    max_inline_bytes=_DEFAULT_INLINE_BYTES,
                    max_output_bytes=_MAX_MODEL_OUTPUT_BYTES,
                    overflow="retain",
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

    def _require_process_controller(self) -> _RunProcessController:
        controller = self._process_controller
        if controller is None:
            raise EnvironmentError(
                "Process operations are unavailable.",
                code="environment_unsupported",
            )
        return controller


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
        "produced_bytes": capture.produced_bytes,
        "producer_complete": capture.producer_complete,
        "content_complete": capture.content_complete,
        "omitted_before_bytes": start_offset,
        "text": data.decode("utf-8", errors="replace"),
    }


def _environment_error_result(error: EnvironmentError) -> ToolFailure:
    details = ToolError(
        code=error.code,
        outcome_known=error.code != "environment_unknown_outcome",
    )
    if error.retry_hint is not None:
        details["retry_hint"] = error.retry_hint
    return {"ok": False, "error": details}


__all__ = ["ShellToolset"]
