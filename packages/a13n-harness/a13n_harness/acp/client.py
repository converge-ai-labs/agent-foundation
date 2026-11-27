"""Run-scoped ACP callbacks over an entered Harness Environment."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from functools import wraps
from inspect import isawaitable
from typing import Any, Concatenate
from uuid import uuid4

from acp import RequestError, schema

from a13n_harness.environment.commands import (
    ArgvCommand,
    BoundProcessHandle,
    CommandEnvironment,
    CommandRequest,
    ProcessStatus,
)
from a13n_harness.environment.models import EnvironmentAction
from a13n_harness.environment.providers import BoundEnvironment
from a13n_harness.providers.environment.direct_local.processes import _settle_task

from .mcp import AcpHostToolBridge, AcpHostToolScope
from .transport import _OUTPUT_PAGE, _PROCESS_OUTPUT, _RUNNING, _stop_process

type AcpUpdate = (
    schema.UserMessageChunk
    | schema.AgentMessageChunk
    | schema.AgentThoughtChunk
    | schema.ToolCallStart
    | schema.ToolCallProgress
    | schema.AgentPlanUpdate
    | schema.AgentPlanContentUpdate
    | schema.AgentPlanRemovedUpdate
    | schema.AvailableCommandsUpdate
    | schema.CurrentModeUpdate
    | schema.ConfigOptionUpdate
    | schema.SessionInfoUpdate
    | schema.UsageUpdate
    | schema.SessionUpdateNotice
    | schema.SessionUpdateCompactionUpdate
    | schema.SessionUpdateCompactionSummaryChunk
)
type ConfigOption = schema.SessionConfigOptionSelect | schema.SessionConfigOptionBoolean


@dataclass
class AcpRunBinding:
    """Host-owned callbacks. None grants no authority for that operation."""

    environment: BoundEnvironment | None = None
    permission: Callable[[schema.ToolCallUpdate, tuple[schema.PermissionOption, ...]], Awaitable[str | None]] | None = (
        None
    )
    elicitation: Callable[[str, str, dict[str, Any]], Awaitable[schema.CreateElicitationResponse]] | None = None
    review_command: Callable[[CommandRequest], Awaitable[bool]] | None = None
    on_update: Callable[[AcpUpdate], Awaitable[None] | None] | None = None
    terminal_alias: str | None = None
    files_enabled: bool = True
    terminals_enabled: bool = True
    host_tools: AcpHostToolScope | None = None
    review_permission_command: (
        Callable[[schema.ToolCallUpdate, tuple[schema.PermissionOption, ...]], Awaitable[str | None]] | None
    ) = None


@dataclass
class _Terminal:
    handle: BoundProcessHandle
    limit: int
    stdout: int = 0
    stderr: int = 0
    tail: bytes = b""
    truncated: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _exit_status(status: ProcessStatus) -> schema.TerminalExitStatus | None:
    if status.phase in _RUNNING:
        return None
    return schema.TerminalExitStatus(
        exit_code=status.exit_code if status.exit_code is not None and status.exit_code >= 0 else None,
        signal=status.signal,
    )


def _scoped_environment_callback[**P, T](
    call: Callable[Concatenate[AcpEnvironmentClient, P], Awaitable[T]],
) -> Callable[Concatenate[AcpEnvironmentClient, P], Awaitable[T]]:
    @wraps(call)
    async def owned(client: AcpEnvironmentClient, *args: P.args, **kwargs: P.kwargs) -> T:
        task = asyncio.current_task()
        assert task is not None
        client._environment_tasks.add(task)
        try:
            return await call(client, *args, **kwargs)
        finally:
            client._environment_tasks.discard(task)

    return owned


class AcpEnvironmentClient:
    """The session may outlive each binding; callbacks never reuse a stale binding."""

    def __init__(self) -> None:
        self.session_id: str | None = None
        self.opening_updates: dict[tuple[str, str], AcpUpdate] | None = None
        self.binding: AcpRunBinding | None = None
        self.config_options: list[ConfigOption] = []
        self._config_options_changed = asyncio.Event()
        self.available_commands: tuple[schema.AvailableCommand, ...] | None = None
        self.session_title: str | None = None
        self.context_window: tuple[int, int] | None = None
        self.context_window_version: int = 0
        self._terminals: dict[str, _Terminal] = {}
        self._tool_calls: dict[str, schema.ToolCallUpdate] = {}
        self._update_error: Exception | None = None
        self._bridge: AcpHostToolBridge | None = None
        self._environment_tasks: set[asyncio.Task[Any]] = set()
        self._interaction_tasks: set[asyncio.Future[Any]] = set()

    def _active(self, session_id: str | None) -> AcpRunBinding:
        if self.binding is None or (session_id is not None and session_id != self.session_id):
            raise RequestError.invalid_request({"code": "acp_run_inactive"})
        return self.binding

    def _environment(self, session_id: str, *, terminal: bool = False) -> BoundEnvironment:
        binding = self._active(session_id)
        environment = binding.environment
        if environment is None or not (binding.terminals_enabled if terminal else binding.files_enabled):
            raise RequestError.invalid_request({"code": "acp_environment_unavailable"})
        return environment

    async def session_update(self, session_id: str, update: AcpUpdate, **_: Any) -> None:
        if session_id != self.session_id:
            if (
                self.opening_updates is not None
                and isinstance(
                    update,
                    schema.ConfigOptionUpdate
                    | schema.AvailableCommandsUpdate
                    | schema.SessionInfoUpdate
                    | schema.UsageUpdate,
                )
                and len(self.opening_updates) < 32
            ):
                self.opening_updates[session_id, update.session_update] = update
            return
        self._remember_session_update(update)
        if self.binding is not None and isinstance(update, schema.ToolCallStart | schema.ToolCallProgress):
            if self.binding.permission is not None or self.binding.review_permission_command is not None:
                if update.status in {"completed", "failed"}:
                    self._tool_calls.pop(update.tool_call_id, None)
                else:
                    fields = update.model_dump(
                        include={"tool_call_id", "title", "kind", "name", "locations", "raw_input"},
                        exclude_unset=True,
                    )
                    self._tool_calls[update.tool_call_id] = self._tool_call(
                        schema.ToolCallUpdate.model_validate(fields)
                    )
            if self._bridge is not None:
                self._bridge._observe(update)
        if self.binding is not None and self.binding.on_update is not None:
            try:
                result = self.binding.on_update(update)
                if isawaitable(result):
                    await result
            except Exception as exc:
                # SDK notification tasks cannot propagate application failure to prompt().
                self._update_error = exc

    def clear_context_window(self) -> None:
        """Invalidate cached context occupancy until a newer usage_update arrives."""
        self.context_window = None
        self.context_window_version += 1

    def _remember_session_update(self, update: AcpUpdate) -> None:
        if isinstance(update, schema.ConfigOptionUpdate):
            self.config_options = update.config_options
            self._config_options_changed.set()
        elif isinstance(update, schema.AvailableCommandsUpdate):
            self.available_commands = tuple(update.available_commands)
        elif isinstance(update, schema.SessionInfoUpdate) and update.title and update.title.strip():
            self.session_title = update.title.strip()
        elif isinstance(update, schema.UsageUpdate) and update.size > 0:
            self.context_window = (update.used, update.size)
            self.context_window_version += 1
        elif isinstance(update, schema.SessionUpdateCompactionUpdate) and update.status == "completed":
            self.clear_context_window()

    def _tool_call(self, call: schema.ToolCallUpdate) -> schema.ToolCallUpdate:
        prior = self._tool_calls.get(call.tool_call_id)
        if prior is None:
            return call
        return call.model_copy(
            update={
                "kind": "execute" if "execute" in (call.kind, prior.kind) else call.kind or prior.kind,
                "title": call.title or prior.title,
                "name": call.name or prior.name,
                "locations": call.locations if "locations" in call.model_fields_set else prior.locations,
                "raw_input": call.raw_input if "raw_input" in call.model_fields_set else prior.raw_input,
            }
        )

    async def request_permission(
        self, session_id: str, tool_call: schema.ToolCallUpdate, options: list[schema.PermissionOption], **_: Any
    ) -> schema.RequestPermissionResponse:
        binding = self._active(session_id)

        async def choose() -> str | None:
            call = self._tool_call(tool_call)
            if call.kind == "execute" and binding.review_permission_command is not None:
                return await binding.review_permission_command(call, tuple(options))
            return await binding.permission(call, tuple(options)) if binding.permission else None

        selected = await self._interact(choose(), binding)
        if selected is not None and selected in {option.option_id for option in options}:
            return schema.RequestPermissionResponse(outcome=schema.AllowedOutcome(option_id=selected))
        return schema.RequestPermissionResponse(outcome=schema.DeniedOutcome())

    async def create_elicitation(
        self, message: str, mode: schema.ElicitationMode, **kwargs: Any
    ) -> schema.CreateElicitationResponse:
        if isinstance(mode, (schema.ElicitationFormSessionMode, schema.ElicitationUrlSessionMode)):
            binding = self._active(mode.session_id)
            params: dict[str, Any] = {"tool_call_id": mode.tool_call_id}
        else:
            binding = self._active(None)
            params = {}
        if binding.elicitation is None:
            return schema.CancelElicitationResponse()
        if isinstance(mode, (schema.ElicitationFormSessionMode, schema.ElicitationFormRequestMode)):
            kind = "form"
            params["requested_schema"] = mode.requested_schema
        elif isinstance(mode, (schema.ElicitationUrlSessionMode, schema.ElicitationUrlRequestMode)):
            kind = "url"
            params.update(url=str(mode.url), elicitation_id=mode.elicitation_id)
        else:
            return schema.CancelElicitationResponse()
        result = await self._interact(binding.elicitation(message, kind, params), binding)
        return result if result is not None else schema.CancelElicitationResponse()

    async def _interact[T](self, operation: Awaitable[T], binding: AcpRunBinding) -> T | None:
        task = asyncio.ensure_future(operation)
        self._interaction_tasks.add(task)
        try:
            result = await task
            return result if self.binding is binding else None
        except asyncio.CancelledError:
            if self.binding is binding:
                raise
            return None
        finally:
            self._interaction_tasks.discard(task)

    def _revoke(self) -> None:
        binding = self.binding
        self.binding = AcpRunBinding(on_update=binding.on_update) if binding is not None else None
        for task in (*self._interaction_tasks, *self._environment_tasks):
            if not isinstance(task, asyncio.Task) or not task.cancelling():
                task.cancel()
        if self._bridge is not None:
            self._bridge._revoke()

    def _mcp(self) -> AcpHostToolBridge:
        if self._bridge is None:
            raise RequestError.resource_not_found()
        return self._bridge

    async def connect_mcp(self, server_id: str, **_: Any) -> schema.ConnectMcpResponse:
        return self._mcp()._connect(server_id)

    async def disconnect_mcp(self, connection_id: str, **_: Any) -> None:
        self._mcp()._disconnect(connection_id)

    async def mcp_message(
        self, connection_id: str, method: str, params: dict[str, Any] | None = None, **metadata: Any
    ) -> dict[str, Any]:
        return await self._mcp()._message(connection_id, method, params or {}, metadata)

    async def notify_mcp(
        self, connection_id: str, method: str, params: dict[str, Any] | None = None, **metadata: Any
    ) -> None:
        if method == "notifications/initialized":
            await self.mcp_message(connection_id, method, params, **metadata)

    @_scoped_environment_callback
    async def read_text_file(
        self, session_id: str, path: str, line: int | None = None, limit: int | None = None, **_: Any
    ) -> schema.ReadTextFileResponse:
        environment = self._environment(session_id)
        selection = environment.select_files(path)
        if line is None and limit is None:
            async with environment.open_files(selection) as files:
                data = await files.read_bytes(path, length=4 * 1024 * 1024 + 1)
            if len(data) > 4 * 1024 * 1024:
                raise RequestError.invalid_params({"code": "acp_file_too_large"})
            return schema.ReadTextFileResponse(content=data.decode())
        chunks = []
        offset = (line or 1) - 1
        remaining = limit
        budget = 4 * 1024 * 1024
        async with environment.open_files(selection) as files:
            while remaining is None or remaining > 0:
                page = await files.read_text(
                    path,
                    line_offset=offset,
                    line_limit=min(remaining or 200, 200),
                    max_line_length=budget,
                    max_bytes=budget,
                )
                if page.truncated_lines or (page.has_more and not page.lines_read):
                    raise RequestError.invalid_params({"code": "acp_file_too_large"})
                chunks.append(page.text)
                if not page.has_more:
                    break
                budget -= len(page.text.encode())
                if budget == 0:
                    raise RequestError.invalid_params({"code": "acp_file_too_large"})
                offset += page.lines_read
                if remaining is not None:
                    remaining -= page.lines_read
        return schema.ReadTextFileResponse(content="".join(chunks))

    @_scoped_environment_callback
    async def write_text_file(self, session_id: str, path: str, content: str, **_: Any) -> None:
        await self._environment(session_id).files.write_text(path, content, mode="upsert")

    @_scoped_environment_callback
    async def create_terminal(
        self,
        session_id: str,
        command: str,
        args: list[str] | None = None,
        env: list[schema.EnvVariable] | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **_: Any,
    ) -> schema.CreateTerminalResponse:
        binding = self._active(session_id)
        environment = self._environment(session_id, terminal=True)
        request = CommandRequest(
            command=ArgvCommand(executable=command, arguments=tuple(args or ())),
            environment=CommandEnvironment(set={v.name: v.value for v in env or ()}),
            cwd=cwd,
            output_policy=_PROCESS_OUTPUT,
        )
        mount_id = None
        if binding.review_command is not None:
            mount_id, can_start = environment._select_command_actions(
                request, alias=binding.terminal_alias, actions=frozenset({EnvironmentAction.PROCESS_START})
            )
            if not can_start or not await self._interact(binding.review_command(request), binding):
                raise RequestError.invalid_request({"code": "acp_command_denied"})
        if self.binding is not binding:
            raise RequestError.invalid_request({"code": "acp_run_inactive"})
        started, cancellation = await _settle_task(
            asyncio.create_task(
                environment.processes.start(request, alias=binding.terminal_alias, expected_mount_id=mount_id)
            )
        )
        handle = started.process.handle
        terminal_id = f"terminal-{uuid4().hex}"
        self._terminals[terminal_id] = _Terminal(
            handle, min(output_byte_limit if output_byte_limit is not None else 65536, _PROCESS_OUTPUT.max_output_bytes)
        )
        if cancellation is not None:
            raise cancellation
        if self.binding is not binding:
            raise RequestError.invalid_request({"code": "acp_run_inactive"})
        return schema.CreateTerminalResponse(terminal_id=terminal_id)

    def _terminal(self, session_id: str, terminal_id: str) -> _Terminal:
        self._active(session_id)
        terminal = self._terminals.get(terminal_id)
        if terminal is None:
            raise RequestError.resource_not_found()
        return terminal

    @_scoped_environment_callback
    async def terminal_output(self, session_id: str, terminal_id: str, **_: Any) -> schema.TerminalOutputResponse:
        terminal = self._terminal(session_id, terminal_id)
        processes = self._environment(session_id, terminal=True).processes
        async with terminal.lock:
            while True:
                page = await processes.read_output(
                    terminal.handle,
                    stdout_start_offset=terminal.stdout,
                    stderr_start_offset=terminal.stderr,
                    policy=_OUTPUT_PAGE,
                )
                for stream_name in ("stdout", "stderr"):
                    stream = page.stdout if stream_name == "stdout" else page.stderr
                    offset = terminal.stdout if stream_name == "stdout" else terminal.stderr
                    for chunk in stream.chunks:
                        terminal.truncated |= chunk.start_offset != offset
                        terminal.tail += chunk.data
                        offset = chunk.start_offset + len(chunk.data)
                    terminal.truncated |= offset < stream.capture.available_start
                    offset = max(offset, stream.capture.available_start)
                    if stream_name == "stdout":
                        terminal.stdout = offset
                    else:
                        terminal.stderr = offset
                if len(terminal.tail) > terminal.limit:
                    terminal.tail = terminal.tail[-terminal.limit :] if terminal.limit else b""
                    terminal.truncated = True
                if (
                    terminal.stdout >= page.stdout.capture.available_end
                    and terminal.stderr >= page.stderr.capture.available_end
                ):
                    break
            # Only the leading fragment of the retained suffix may be a split UTF-8 code point.
            tail = terminal.tail.lstrip(bytes(range(0x80, 0xC0))) if terminal.truncated else terminal.tail
            return schema.TerminalOutputResponse(
                output=tail.decode(errors="replace"),
                truncated=terminal.truncated,
                exit_status=_exit_status(page.process.status),
            )

    @_scoped_environment_callback
    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **_: Any
    ) -> schema.WaitForTerminalExitResponse:
        terminal = self._terminal(session_id, terminal_id)
        processes = self._environment(session_id, terminal=True).processes
        while True:
            info = await processes.wait(terminal.handle, condition="initial_terminal", timeout_seconds=1)
            if (status := _exit_status(info.status)) is not None:
                return schema.WaitForTerminalExitResponse(exit_code=status.exit_code, signal=status.signal)

    @_scoped_environment_callback
    async def kill_terminal(self, session_id: str, terminal_id: str, **_: Any) -> None:
        terminal = self._terminal(session_id, terminal_id)
        await self._environment(session_id, terminal=True).processes.kill(terminal.handle)

    @_scoped_environment_callback
    async def release_terminal(self, session_id: str, terminal_id: str, **_: Any) -> None:
        terminal = self._terminal(session_id, terminal_id)
        processes = self._environment(session_id, terminal=True).processes
        async with terminal.lock:
            if terminal_id in self._terminals:

                async def release() -> None:
                    await _stop_process(processes, terminal.handle)
                    del self._terminals[terminal_id]

                _released, cancellation = await _settle_task(asyncio.create_task(release()))
                if cancellation is not None:
                    raise cancellation

    async def _unbind(self, binding: AcpRunBinding) -> None:
        self._revoke()
        self.binding = None
        self._tool_calls.clear()
        await asyncio.gather(*self._interaction_tasks, *self._environment_tasks, return_exceptions=True)
        if self._bridge is not None:
            await self._bridge._unbind()
        terminals, self._terminals = self._terminals, {}
        if binding.environment is not None:
            async with AsyncExitStack() as cleanup:
                for terminal in terminals.values():
                    cleanup.push_async_callback(_stop_process, binding.environment.processes, terminal.handle)
