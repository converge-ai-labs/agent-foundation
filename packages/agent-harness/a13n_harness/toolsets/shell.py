"""Reusable compact model-facing shell and process Toolset."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any, Literal, cast

from pydantic import Field, JsonValue
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from a13n_harness.capabilities.processes import ShellOperator
from a13n_harness.context import AgentContext
from a13n_harness.environment.commands import (
    CommandEnvironment,
    CommandLimits,
    CommandRequest,
    ShellCommand,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.retention import EnvironmentOutputPolicy
from a13n_harness.tools.metadata import (
    HarnessTool,
    HarnessToolMetadata,
    ToolEffect,
    ToolOutputPolicy,
    ToolResourceResolver,
)

from ._instructions import InstructionFunctionToolset, tool_instruction
from ._results import ToolFailure
from .output import (
    DEFAULT_TOOL_OUTPUT_CHARS,
    disclose_sequence_field,
    disclose_text_paths,
)
from .process_manager import (
    _capture_initial_bytes,
    _ProcessRunManager,
    _project_capture,
    _project_status,
)
from .shell_results import (
    ProcessInputResult,
    ProcessReadOutputResult,
    ProcessSignalResult,
    ProcessStatusListResult,
    ShellExecToolResult,
)

_MAX_MODEL_OUTPUT_BYTES = 1024 * 1024
_MAX_MODEL_RESULTS = 1_000
_DEFAULT_INLINE_BYTES = 64 * 1024
_MAX_REFERENCE_ENTRIES = 100_000

_SHELL_INSTRUCTION = tool_instruction("environment-shell")

_PositiveResults = Annotated[int, Field(gt=0, le=_MAX_MODEL_RESULTS)]
_NonNegativeOffset = Annotated[int, Field(ge=0)]
_PositiveTimeout = Annotated[float, Field(gt=0, allow_inf_nan=False)]
_NonNegativeTimeout = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class ShellToolset:
    """Six concise shell/process tools over provider-neutral Environment ports."""

    def __init__(
        self,
        *,
        operator: ShellOperator | None = None,
        supports_background: bool | None = None,
        resource_resolver: Callable[[str], ToolResourceResolver] | None = None,
        execution_guard: Callable[[], None] | None = None,
    ) -> None:
        selected = operator or ShellOperator()
        if not isinstance(selected, ShellOperator):
            raise TypeError("operator must be a ShellOperator")
        background = selected.supports_background if supports_background is None else supports_background
        if type(background) is not bool:
            raise TypeError("supports_background must be a boolean")
        self._operator = selected
        self._resource_resolver = resource_resolver
        self._execution_guard = execution_guard
        self._process_manager = (
            _ProcessRunManager(operator=selected, execution_guard=execution_guard) if background else None
        )

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        arbitrary_command_effects: set[ToolEffect] = {
            "read",
            "write",
            "delete",
            "execute",
            "external_communication",
        }
        shell_callable = self.shell_exec if self._process_manager is not None else self.shell_exec_foreground
        tools: list[HarnessTool] = [
            self._tool(
                shell_callable,
                "environment.shell_exec",
                arbitrary_command_effects,
                "none",
                name="shell_exec",
            )
        ]
        if self._process_manager is not None:
            tools.extend(
                (
                    self._tool(self.shell_wait, "environment.process_wait", {"read"}, "none"),
                    self._tool(self.shell_status, "environment.process_status", {"read"}, "read_only"),
                    self._tool(self.shell_input, "environment.process_input", {"write"}, "none"),
                    self._tool(self.shell_signal, "environment.process_signal", {"execute"}, "none"),
                    self._tool(
                        self.shell_kill,
                        "environment.process_kill",
                        {"read", "delete", "execute"},
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
        if self._process_manager is None:
            return await handler()
        async with self._process_manager.active_run(ctx):
            return await handler()

    def resolve_process_resource(self, process_id: str) -> str:
        """Resolve a compact process ID to its private Host backend reference."""
        return self._require_process_manager().resource_id(process_id)

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
        """Execute one foreground command and return its captured result."""
        return await self.shell_exec(
            ctx,
            command,
            cwd=cwd,
            environment=environment,
            timeout_seconds=timeout_seconds,
            background=False,
            alias=alias,
        )

    async def shell_exec(
        self,
        ctx: RunContext[AgentContext],
        command: str,
        *,
        cwd: str | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: _PositiveTimeout | None = None,
        background: bool = False,
        alias: str | None = None,
    ) -> ShellExecToolResult:
        """Execute a command in the foreground or start a real background process."""
        try:
            self._guard_execution()
            request = self._command_request(
                command,
                cwd=cwd,
                environment=environment,
                timeout_seconds=timeout_seconds,
                keep_stdin_open=background,
            )
            if background:
                manager = self._require_process_manager()
                projected = await manager.start(request, alias=alias)
                return cast(ShellExecToolResult, {"ok": True, "background": True, **projected})
            result = await self._operator.execute(ctx.deps, request, alias)
            stdout = _project_capture(result.output.stdout, _capture_initial_bytes(result.output.stdout))
            stderr = _project_capture(result.output.stderr, _capture_initial_bytes(result.output.stderr))
            projected = cast(
                dict[str, JsonValue],
                {
                    "ok": True,
                    "background": False,
                    "status": _project_status(result.status),
                    "stdout": stdout,
                    "stderr": stderr,
                },
            )
            content_complete = stdout["content_complete"] and stderr["content_complete"]
            return cast(
                ShellExecToolResult,
                await disclose_text_paths(
                    # Tool output spill uses the active Agent dependencies only for foreground output.
                    cast(AgentContext, ctx.deps),
                    projected,
                    text_paths=(("stdout", "text"), ("stderr", "text")),
                    content_complete=content_complete,
                    noun="foreground command result",
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
        timeout_seconds: _NonNegativeTimeout = 180,
    ) -> ProcessReadOutputResult:
        """Wait boundedly, or poll with zero, and drain the next output page."""
        del ctx
        try:
            return await self._require_process_manager().wait(
                process_id,
                timeout_seconds=timeout_seconds,
            )
        except EnvironmentError as exc:
            return cast(ProcessReadOutputResult, _environment_error_result(exc))

    async def shell_status(
        self,
        ctx: RunContext[AgentContext],
        *,
        cursor: _NonNegativeOffset = 0,
        limit: _PositiveResults = 100,
    ) -> ProcessStatusListResult:
        """Inspect one metadata-only bounded page of managed Thread processes."""
        try:
            projected = await self._require_process_manager().status(cursor=cursor, limit=limit)
            processes = projected["processes"]
            bounded, showing = await disclose_sequence_field(
                ctx.deps,
                cast(dict[str, JsonValue], projected),
                field="processes",
                content_complete=projected["next_cursor"] is None,
                noun="process status page",
                continuation_hint="Call shell_status again with next_cursor as cursor to continue.",
            )
            bounded["showing"] = showing
            disclosure = bounded.get("disclosure")
            if showing < len(processes) and isinstance(disclosure, dict) and disclosure.get("output_file_path") is None:
                bounded["next_cursor"] = cursor
                bounded["truncated"] = True
                disclosure["hint"] = "Call shell_status again with this next_cursor and a smaller limit."
            return cast(ProcessStatusListResult, bounded)
        except EnvironmentError as exc:
            return cast(ProcessStatusListResult, _environment_error_result(exc))

    async def shell_input(
        self,
        ctx: RunContext[AgentContext],
        process_id: str,
        data: str = "",
        *,
        close_stdin: bool = False,
    ) -> ProcessInputResult:
        """Write UTF-8 stdin and optionally send EOF in the same call."""
        del ctx
        try:
            accepted_bytes, stdin_open = await self._require_process_manager().write_input(
                process_id,
                data,
                close_stdin=close_stdin,
            )
            return {"ok": True, "accepted_bytes": accepted_bytes, "stdin_open": stdin_open}
        except EnvironmentError as exc:
            return cast(ProcessInputResult, _environment_error_result(exc))

    async def shell_signal(
        self,
        ctx: RunContext[AgentContext],
        process_id: str,
        signal: Literal["interrupt", "terminate"],
    ) -> ProcessSignalResult:
        """Send one portable semantic signal to a background process."""
        del ctx
        try:
            accepted, process = await self._require_process_manager().signal(process_id, signal)
            return cast(ProcessSignalResult, {"ok": True, "accepted": accepted, **process})
        except EnvironmentError as exc:
            return cast(ProcessSignalResult, _environment_error_result(exc))

    async def shell_kill(
        self,
        ctx: RunContext[AgentContext],
        process_id: str,
    ) -> ProcessReadOutputResult:
        """Force process-tree termination and return the next final output page."""
        del ctx
        try:
            return await self._require_process_manager().kill(process_id)
        except EnvironmentError as exc:
            return cast(ProcessReadOutputResult, _environment_error_result(exc))

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

    def _require_process_manager(self) -> _ProcessRunManager:
        if self._process_manager is None:
            raise EnvironmentError(
                "Background process operations are unavailable.",
                code="environment_unsupported",
            )
        return self._process_manager


def _environment_error_result(error: EnvironmentError) -> ToolFailure:
    return {
        "ok": False,
        "error": {
            "code": error.code,
            "retry_hint": error.retry_hint,
            "outcome_known": error.code != "environment_unknown_outcome",
        },
    }


__all__ = ["ShellToolset"]
