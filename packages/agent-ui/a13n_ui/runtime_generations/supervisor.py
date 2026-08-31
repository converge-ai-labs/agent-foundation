"""Serialized fresh-process lifecycle for Agent UI runtime Runners."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import sys
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import JsonValue, TypeAdapter

from a13n_ui.configuration import ConfigurationSettings
from a13n_ui.errors import RuntimeGenerationError
from a13n_ui.runtime_settings import RuntimeGenerationSettings
from a13n_ui.settings import EnvdRuntimeSettings, StorageSettings

from .models import (
    RuntimeDiagnostic,
    RuntimeExitReason,
    RuntimeGenerationObservation,
    RuntimeGenerationState,
    RuntimeReadiness,
    RuntimeRestartResult,
    RuntimeStatus,
)
from .protocol import (
    ControlChannel,
    ControlProtocolError,
    require_generation,
    require_string,
    require_string_list,
)
from .runner import current_runtime_readiness
from .wire import (
    ExecuteEnvironmentCommand,
    ExecuteRootRun,
    ProviderStateUpdate,
    RunnerAsyncWorkEvent,
    RunnerEnvironmentResult,
    RunnerRunResult,
)

_RunEventCallback = Callable[[str, tuple[JsonValue, ...]], Awaitable[None]]
_ProviderStateCallback = Callable[[ProviderStateUpdate], Awaitable[None]]
_AsyncWorkCallback = Callable[[RunnerAsyncWorkEvent], Awaitable[None]]


@dataclass(slots=True)
class _HostExecution:
    result: asyncio.Future[RunnerRunResult]
    on_event: _RunEventCallback
    on_provider_state: _ProviderStateCallback


@dataclass(slots=True)
class _HostEnvironmentCommand:
    result: asyncio.Future[RunnerEnvironmentResult]
    on_provider_state: _ProviderStateCallback


@dataclass(slots=True)
class _RunnerGeneration:
    generation_id: str
    process: asyncio.subprocess.Process
    channel: ControlChannel
    observation: RuntimeGenerationObservation
    watcher: asyncio.Task[None] | None = None
    reader: asyncio.Task[None] | None = None
    selected_exit_reason: RuntimeExitReason | None = None
    lifecycle_waiters: dict[str, asyncio.Future[dict[str, Any]]] = None  # type: ignore[assignment]
    executions: dict[str, _HostExecution] = None  # type: ignore[assignment]
    environment_commands: dict[str, _HostEnvironmentCommand] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.lifecycle_waiters = {}
        self.executions = {}
        self.environment_commands = {}


class RuntimeGenerationService:
    """Own one active Runner and one bounded candidate during restart."""

    def __init__(
        self,
        settings: RuntimeGenerationSettings,
        *,
        command: Sequence[str] | None = None,
        environment: dict[str, str] | None = None,
        storage: StorageSettings | None = None,
        configuration: ConfigurationSettings | None = None,
        envd_runtime: EnvdRuntimeSettings | None = None,
        on_async_work: _AsyncWorkCallback | None = None,
    ) -> None:
        self._settings = settings
        self._command = tuple(command or (sys.executable, "-m", "a13n_ui.runtime_runner"))
        if not self._command or any(not isinstance(item, str) or not item for item in self._command):
            raise ValueError("runtime Runner command must be a non-empty exact argument vector")
        self._environment = dict(environment or {})
        self._storage = storage
        self._configuration = configuration
        self._envd_runtime = envd_runtime
        self._on_async_work = on_async_work
        self._lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._active: _RunnerGeneration | None = None
        self._candidate: _RunnerGeneration | None = None
        self._draining: dict[str, _RunnerGeneration] = {}
        self._observations: dict[str, RuntimeGenerationObservation] = {}
        self._diagnostics: list[RuntimeDiagnostic] = []
        self._closed = False

    def set_async_work_handler(self, callback: _AsyncWorkCallback) -> None:
        """Bind the one stable Host callback before asynchronous work is admitted."""

        if not callable(callback):
            raise TypeError("async work callback must be callable")
        if self._on_async_work is not None and self._on_async_work is not callback:
            raise RuntimeError("async work callback is already configured")
        self._on_async_work = callback

    async def start(self) -> RuntimeGenerationObservation:
        """Start and promote the initial runtime Runner."""

        async with self._lifecycle_lock:
            async with self._lock:
                if self._closed:
                    raise self._error("runtime_service_closed", "Runtime supervision is closed.")
                if self._active is not None:
                    return self._active.observation
                candidate = await self._start_candidate()
                try:
                    await self._activate(candidate)
                except BaseException:
                    await _complete_cleanup(self._stop_candidate(candidate))
                    raise
                self._active = candidate
                self._candidate = None
                return candidate.observation

    async def restart(self) -> RuntimeRestartResult:
        """Promote a fresh Runner before draining the previously selected generation."""

        async with self._lifecycle_lock:
            async with self._lock:
                if self._closed:
                    raise self._error("runtime_service_closed", "Runtime supervision is closed.")
                previous = self._active
                candidate = await self._start_candidate()
                try:
                    await self._activate(candidate)
                except BaseException as exc:
                    await _complete_cleanup(self._stop_candidate(candidate))
                    if isinstance(exc, asyncio.CancelledError | RuntimeGenerationError):
                        raise
                    raise self._error(
                        "runtime_candidate_failed",
                        "The candidate runtime Runner failed before promotion.",
                        generation_id=candidate.generation_id,
                    ) from exc
                self._active = candidate
                self._candidate = None
                if previous is not None:
                    self._draining[previous.generation_id] = previous
            if previous is not None:
                try:
                    cancelled = await _complete_cleanup(self._drain_and_stop(previous))
                    if cancelled:
                        raise asyncio.CancelledError
                finally:
                    self._draining.pop(previous.generation_id, None)
            return RuntimeRestartResult(
                previous_generation_id=previous.generation_id if previous is not None else None,
                active=candidate.observation,
            )

    async def execute_root(
        self,
        request: ExecuteRootRun,
        *,
        on_event: _RunEventCallback,
        on_provider_state: _ProviderStateCallback,
    ) -> RunnerRunResult:
        """Admit one Run to the currently selected generation and await its terminal result."""

        async with self._lock:
            runner = self._active
            if (
                self._closed
                or runner is None
                or runner.observation.state is not RuntimeGenerationState.active
                or runner.process.returncode is not None
            ):
                raise self._error("runtime_unavailable", "No active runtime Runner is available.")
            bound = request.model_copy(update={"generation_id": runner.generation_id})
            if bound.request_id in runner.executions:
                raise self._error("runtime_request_duplicate", "The runtime request ID is already active.")
            future: asyncio.Future[RunnerRunResult] = asyncio.get_running_loop().create_future()
            execution = _HostExecution(
                result=future,
                on_event=on_event,
                on_provider_state=on_provider_state,
            )
            runner.executions[bound.request_id] = execution
            try:
                await runner.channel.send(
                    "EXECUTE_ROOT",
                    generation_id=runner.generation_id,
                    request=bound.model_dump(mode="json"),
                )
            except BaseException:
                runner.executions.pop(bound.request_id, None)
                raise
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await asyncio.shield(self.cancel_root(bound.request_id))
            raise

    async def execute_environment(
        self,
        request: ExecuteEnvironmentCommand,
        *,
        on_provider_state: _ProviderStateCallback,
    ) -> RunnerEnvironmentResult:
        """Admit one Environment lifecycle command to the active generation."""

        async with self._lock:
            runner = self._active
            if (
                self._closed
                or runner is None
                or runner.observation.state is not RuntimeGenerationState.active
                or runner.process.returncode is not None
            ):
                raise self._error("runtime_unavailable", "No active runtime Runner is available.")
            bound = request.model_copy(update={"generation_id": runner.generation_id})
            if bound.request_id in runner.environment_commands or bound.request_id in runner.executions:
                raise self._error("runtime_request_duplicate", "The runtime request ID is already active.")
            future: asyncio.Future[RunnerEnvironmentResult] = asyncio.get_running_loop().create_future()
            command = _HostEnvironmentCommand(
                result=future,
                on_provider_state=on_provider_state,
            )
            runner.environment_commands[bound.request_id] = command
            future.add_done_callback(lambda _future: _remove_environment_command(runner, bound.request_id, command))
            try:
                await runner.channel.send(
                    "EXECUTE_ENVIRONMENT",
                    generation_id=runner.generation_id,
                    request=bound.model_dump(mode="json"),
                )
            except BaseException:
                runner.environment_commands.pop(bound.request_id, None)
                raise
        return await asyncio.shield(future)

    async def cancel_root(self, request_id: str) -> bool:
        """Request cancellation of one active generation-bound root Run."""

        async with self._lock:
            runners = tuple(
                item for item in (self._active, self._candidate, *self._draining.values()) if item is not None
            )
            runner = next((item for item in runners if request_id in item.executions), None)
            if runner is None or runner.process.returncode is not None:
                return False
        response = await self._request(
            runner,
            "CANCEL_ROOT",
            "CANCEL_ROOT_RESULT",
            request_id=request_id,
        )
        return response.get("accepted") is True

    async def status(self) -> RuntimeStatus:
        """Return detached current routing and bounded retained observations."""

        self._refresh_return_codes()
        observations = tuple(
            sorted(self._observations.values(), key=lambda item: item.started_at, reverse=True)[
                : self._settings.retained_generations
            ]
        )
        diagnostics = tuple(self._diagnostics[-self._settings.retained_diagnostics :])
        active_generation_id = (
            self._active.generation_id
            if self._active is not None and self._active.observation.state is RuntimeGenerationState.active
            else None
        )
        candidate_generation_id = (
            self._candidate.generation_id
            if self._candidate is not None and self._candidate.observation.state is not RuntimeGenerationState.exited
            else None
        )
        return RuntimeStatus(
            active_generation_id=active_generation_id,
            candidate_generation_id=candidate_generation_id,
            generations=observations,
            diagnostics=diagnostics,
        )

    async def close(self) -> None:
        """Idempotently stop candidate and active Runners with bounded escalation."""

        async with self._lifecycle_lock:
            async with self._lock:
                if self._closed:
                    return
                candidate = self._candidate
                active = self._active
                self._candidate = None
                self._active = None
                self._closed = True
            cancelled = await _complete_cleanup(self._close_generations(candidate, active))
            if cancelled:
                raise asyncio.CancelledError

    async def _close_generations(
        self,
        candidate: _RunnerGeneration | None,
        active: _RunnerGeneration | None,
    ) -> None:
        if candidate is not None:
            await self._stop_candidate(candidate)
        if active is not None:
            await self._drain_and_stop(active)

    async def _start_candidate(self) -> _RunnerGeneration:
        generation_id = f"runtime-{secrets.token_hex(6)}"
        now = _now()
        observation = RuntimeGenerationObservation(
            generation_id=generation_id,
            state=RuntimeGenerationState.starting,
            started_at=now,
            updated_at=now,
        )
        self._retain_observation(observation)

        token_holder = [secrets.token_urlsafe(32)]
        accepted: asyncio.Future[ControlChannel] = asyncio.get_running_loop().create_future()

        async def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            channel = ControlChannel(reader, writer, max_message_bytes=self._settings.max_message_bytes)
            try:
                message = await asyncio.wait_for(
                    channel.receive(expected_type="HELLO"), timeout=self._settings.command_timeout_seconds
                )
                received_generation = require_string(message, "generation_id", max_length=64)
                received_token = require_string(message, "token", max_length=256)
                expected_token = token_holder[0]
                if (
                    not expected_token
                    or received_generation != generation_id
                    or not secrets.compare_digest(received_token, expected_token)
                ):
                    raise ControlProtocolError("Runner launch authentication failed")
                if accepted.done():
                    raise ControlProtocolError("Runner control channel is already connected")
                token_holder[0] = ""
                await channel.send("WELCOME", generation_id=generation_id)
                accepted.set_result(channel)
            except BaseException as exc:
                await channel.close()
                if isinstance(exc, asyncio.CancelledError):
                    raise

        server = await asyncio.start_server(
            accept,
            host="127.0.0.1",
            port=0,
            limit=self._settings.max_message_bytes,
            start_serving=True,
        )
        sockets = server.sockets or ()
        if len(sockets) != 1:
            server.close()
            await server.wait_closed()
            raise self._error("runtime_listener_failed", "The private Runner listener could not be created.")
        port = int(sockets[0].getsockname()[1])
        environment = os.environ.copy()
        environment.update(self._environment)
        environment.update(
            {
                "A13N_UI_RUNNER_CONTROL_HOST": "127.0.0.1",
                "A13N_UI_RUNNER_CONTROL_PORT": str(port),
                "A13N_UI_RUNNER_CONTROL_TOKEN": token_holder[0],
                "A13N_UI_RUNNER_GENERATION_ID": generation_id,
                "A13N_UI_RUNNER_MAX_MESSAGE_BYTES": str(self._settings.max_message_bytes),
            }
        )
        if self._storage is not None and self._configuration is not None and self._envd_runtime is not None:
            environment["A13N_UI_RUNNER_STORAGE"] = self._storage.model_dump_json()
            environment["A13N_UI_RUNNER_CONFIGURATION"] = self._configuration.model_dump_json()
            environment["A13N_UI_RUNNER_ENVD_RUNTIME"] = self._envd_runtime.model_dump_json()
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command,
                env=environment,
                cwd=str(Path.cwd()),
                start_new_session=os.name != "nt",
            )
            channel = await self._await_connection(process, accepted)
            ready = await asyncio.wait_for(
                channel.receive(expected_type="READY"), timeout=self._settings.command_timeout_seconds
            )
            require_generation(ready, generation_id)
            readiness = RuntimeReadiness(
                protocol_version=require_string(ready, "protocol_version", max_length=32),
                agent_ui_version=require_string(ready, "agent_ui_version", max_length=64),
                python_version=require_string(ready, "python_version", max_length=64),
                loaded_provenance=require_string_list(ready, "loaded_provenance"),
            )
            if readiness != current_runtime_readiness():
                raise self._error(
                    "runtime_readiness_mismatch",
                    "The candidate runtime provenance does not match the Host selection.",
                    generation_id=generation_id,
                )
            observation = observation.model_copy(
                update={
                    "process_id": process.pid,
                    "state": RuntimeGenerationState.ready,
                    "updated_at": _now(),
                    "readiness": readiness,
                }
            )
            runner = _RunnerGeneration(
                generation_id=generation_id,
                process=process,
                channel=channel,
                observation=observation,
            )
            self._candidate = runner
            self._publish(runner)
            runner.reader = asyncio.create_task(self._read_runner(runner), name=f"read-{generation_id}")
            runner.watcher = asyncio.create_task(self._watch_exit(runner), name=f"watch-{generation_id}")
            return runner
        except BaseException as exc:
            cleanup_cancelled = False
            if process is not None and process.returncode is None:

                async def kill_started_process() -> None:
                    process.kill()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=self._settings.kill_timeout_seconds)
                    except TimeoutError as timeout:
                        raise self._error(
                            "runtime_kill_timeout",
                            "The runtime Runner did not exit after startup kill.",
                            generation_id=generation_id,
                        ) from timeout

                cleanup_cancelled = await _complete_cleanup(kill_started_process())
            self._record_diagnostic(
                generation_id,
                "runtime_start_failed",
                "The runtime Runner did not complete authenticated process readiness.",
            )
            self._retain_observation(
                observation.model_copy(
                    update={
                        "state": RuntimeGenerationState.exited,
                        "updated_at": _now(),
                        "exit_reason": RuntimeExitReason.startup_failed,
                        "return_code": process.returncode if process is not None else None,
                    }
                )
            )
            if isinstance(exc, asyncio.CancelledError) or cleanup_cancelled:
                raise asyncio.CancelledError from exc
            raise self._error(
                "runtime_start_failed",
                "The runtime Runner did not become ready.",
                generation_id=generation_id,
            ) from exc
        finally:
            # Python 3.13 Server.wait_closed() can wait for accepted client
            # connections. The authenticated Runner channel intentionally stays
            # open after the listening socket closes.
            server.close()

    async def _await_connection(
        self,
        process: asyncio.subprocess.Process,
        accepted: asyncio.Future[ControlChannel],
    ) -> ControlChannel:
        process_exit = asyncio.create_task(process.wait())
        try:
            done, _ = await asyncio.wait(
                {accepted, process_exit},
                timeout=self._settings.startup_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if accepted in done:
                return accepted.result()
            if process_exit in done:
                raise self._error("runtime_early_exit", "The runtime Runner exited before authentication.")
            raise self._error("runtime_start_timeout", "The runtime Runner authentication timed out.")
        finally:
            if not process_exit.done():
                process_exit.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await process_exit

    async def _read_runner(self, runner: _RunnerGeneration) -> None:
        error: BaseException | None = None
        try:
            while True:
                message = await runner.channel.receive()
                require_generation(message, runner.generation_id)
                message_type = require_string(message, "type", max_length=64)
                response_id = message.get("request_id")
                waiter_key = f"{message_type}:{response_id}" if isinstance(response_id, str) else message_type
                waiter = runner.lifecycle_waiters.pop(waiter_key, None)
                if waiter is not None:
                    if not waiter.done():
                        waiter.set_result(message)
                    continue
                if message_type == "RUN_EVENT":
                    request_id = require_string(message, "request_id", max_length=64)
                    run_id = require_string(message, "run_id", max_length=128)
                    execution = runner.executions.get(request_id)
                    events = message.get("events")
                    if execution is None or not isinstance(events, list):
                        raise ControlProtocolError("execution event does not match an active request")
                    validated = TypeAdapter(tuple[JsonValue, ...]).validate_python(tuple(events))
                    await execution.on_event(run_id, validated)
                    continue
                if message_type == "ASYNC_WORK_EVENT":
                    event = RunnerAsyncWorkEvent.model_validate_json(json.dumps(message.get("event")), strict=True)
                    callback = self._on_async_work
                    if callback is not None:
                        try:
                            await callback(event)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            self._record_diagnostic(
                                runner.generation_id,
                                "async_work_callback_failed",
                                "The stable Host rejected an asynchronous work notification.",
                            )
                    continue
                if message_type == "PROVIDER_STATE_UPDATE":
                    update = ProviderStateUpdate.model_validate_json(json.dumps(message.get("update")), strict=True)
                    execution = runner.executions.get(update.request_id)
                    command = runner.environment_commands.get(update.request_id)
                    if execution is None and command is None:
                        raise ControlProtocolError("provider state update does not match an active request")
                    if execution is not None:
                        callback = execution.on_provider_state
                    else:
                        assert command is not None
                        callback = command.on_provider_state
                    try:
                        await callback(update)
                    except BaseException:
                        await runner.channel.send(
                            "PROVIDER_STATE_FAILED",
                            generation_id=runner.generation_id,
                            update_id=update.update_id,
                        )
                    else:
                        await runner.channel.send(
                            "PROVIDER_STATE_ACK",
                            generation_id=runner.generation_id,
                            update_id=update.update_id,
                        )
                    continue
                if message_type == "RUN_RESULT":
                    result = RunnerRunResult.model_validate_json(json.dumps(message.get("result")), strict=True)
                    execution = runner.executions.get(result.request_id)
                    if execution is None or execution.result.done():
                        raise ControlProtocolError("terminal result does not match an active request")
                    execution.result.set_result(result)
                    continue
                if message_type == "RUN_SCOPE_CLOSED":
                    request_id = require_string(message, "request_id", max_length=64)
                    execution = runner.executions.get(request_id)
                    if execution is None or not execution.result.done():
                        raise ControlProtocolError("closed Run scope does not match a terminal request")
                    _remove_execution(runner, request_id, execution)
                    continue
                if message_type == "ENVIRONMENT_RESULT":
                    result = RunnerEnvironmentResult.model_validate_json(json.dumps(message.get("result")), strict=True)
                    command = runner.environment_commands.get(result.request_id)
                    if command is None or command.result.done():
                        raise ControlProtocolError("Environment result does not match an active request")
                    command.result.set_result(result)
                    continue
                raise ControlProtocolError(f"unexpected Runner message: {message_type}")
        except BaseException as exc:
            error = exc
            if isinstance(exc, asyncio.CancelledError):
                raise
            if runner.observation.state in {
                RuntimeGenerationState.ready,
                RuntimeGenerationState.active,
            }:
                self._transition(runner, RuntimeGenerationState.draining)
            self._record_diagnostic(
                runner.generation_id,
                "runtime_channel_failed",
                "The runtime Runner control channel failed.",
            )
            await runner.channel.close()
        finally:
            if error is None:
                error = ControlProtocolError("Runner channel reader stopped")
            for waiter in tuple(runner.lifecycle_waiters.values()):
                if not waiter.done():
                    waiter.set_exception(error)
            runner.lifecycle_waiters.clear()
            lost = self._error(
                "runtime_runner_lost",
                "The runtime Runner exited before returning a terminal result.",
                generation_id=runner.generation_id,
            )
            for execution in tuple(runner.executions.values()):
                if not execution.result.done():
                    execution.result.set_exception(lost)
            for command in tuple(runner.environment_commands.values()):
                if not command.result.done():
                    command.result.set_exception(lost)
            runner.executions.clear()
            runner.environment_commands.clear()

    async def _request(
        self,
        runner: _RunnerGeneration,
        message_type: str,
        expected_type: str,
        **fields: object,
    ) -> dict[str, Any]:
        correlation = fields.get("request_id")
        waiter_key = f"{expected_type}:{correlation}" if isinstance(correlation, str) else expected_type
        if waiter_key in runner.lifecycle_waiters:
            raise ControlProtocolError(f"a {expected_type} response is already pending")
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        runner.lifecycle_waiters[waiter_key] = future
        try:
            await runner.channel.send(message_type, generation_id=runner.generation_id, **fields)
            return await future
        finally:
            if runner.lifecycle_waiters.get(waiter_key) is future:
                runner.lifecycle_waiters.pop(waiter_key, None)

    async def _activate(self, candidate: _RunnerGeneration) -> None:
        try:
            active = await asyncio.wait_for(
                self._request(candidate, "ACTIVATE", "ACTIVE"),
                timeout=self._settings.command_timeout_seconds,
            )
            require_generation(active, candidate.generation_id)
            self._transition(candidate, RuntimeGenerationState.active)
        except (TimeoutError, ControlProtocolError, OSError) as exc:
            raise self._error(
                "runtime_promotion_failed",
                "The candidate runtime Runner failed before activation.",
                generation_id=candidate.generation_id,
            ) from exc

    async def _stop_candidate(self, candidate: _RunnerGeneration) -> None:
        candidate.selected_exit_reason = RuntimeExitReason.startup_failed
        if candidate.process.returncode is None:
            with contextlib.suppress(Exception):
                exiting = await asyncio.wait_for(
                    self._request(candidate, "SHUTDOWN", "EXITING"),
                    timeout=self._settings.command_timeout_seconds,
                )
                require_generation(exiting, candidate.generation_id)
        await self._ensure_stopped(candidate, RuntimeExitReason.startup_failed)
        if self._candidate is candidate:
            self._candidate = None

    async def _drain_and_stop(self, runner: _RunnerGeneration) -> None:
        if runner.process.returncode is not None:
            await self._finish_watcher(runner)
            if runner.observation.state is not RuntimeGenerationState.exited:
                self._mark_exited(runner, RuntimeExitReason.unexpected)
            await runner.channel.close()
            await self._finish_reader(runner)
            return
        if runner.observation.state is RuntimeGenerationState.exited:
            await runner.channel.close()
            await self._finish_reader(runner)
            await self._finish_watcher(runner)
            return
        runner.selected_exit_reason = RuntimeExitReason.graceful
        if runner.process.returncode is None and runner.observation.state is RuntimeGenerationState.active:
            self._transition(runner, RuntimeGenerationState.draining)
            try:
                drained = await asyncio.wait_for(
                    self._request(runner, "DRAIN", "DRAINED"),
                    timeout=self._settings.drain_timeout_seconds,
                )
                require_generation(drained, runner.generation_id)
                exiting = await asyncio.wait_for(
                    self._request(runner, "SHUTDOWN", "EXITING"),
                    timeout=self._settings.command_timeout_seconds,
                )
                require_generation(exiting, runner.generation_id)
            except (TimeoutError, ControlProtocolError, OSError):
                self._record_diagnostic(
                    runner.generation_id,
                    "runtime_drain_failed",
                    "The runtime Runner did not complete graceful drain and shutdown.",
                )
        await self._ensure_stopped(runner, RuntimeExitReason.graceful)

    async def _ensure_stopped(self, runner: _RunnerGeneration, graceful_reason: RuntimeExitReason) -> None:
        reason = graceful_reason
        if runner.process.returncode is None:
            try:
                await asyncio.wait_for(runner.process.wait(), timeout=self._settings.command_timeout_seconds)
            except TimeoutError:
                reason = RuntimeExitReason.terminated
                runner.selected_exit_reason = reason
                runner.process.terminate()
                try:
                    await asyncio.wait_for(runner.process.wait(), timeout=self._settings.terminate_timeout_seconds)
                except TimeoutError:
                    reason = RuntimeExitReason.killed
                    runner.selected_exit_reason = reason
                    runner.process.kill()
                    try:
                        await asyncio.wait_for(runner.process.wait(), timeout=self._settings.kill_timeout_seconds)
                    except TimeoutError as exc:
                        raise self._error(
                            "runtime_kill_timeout",
                            "The runtime Runner did not exit after kill.",
                            generation_id=runner.generation_id,
                        ) from exc
        await runner.channel.close()
        await self._finish_reader(runner)
        if runner.observation.state is not RuntimeGenerationState.exited:
            self._mark_exited(runner, reason)
        await self._finish_watcher(runner)

    async def _watch_exit(self, runner: _RunnerGeneration) -> None:
        return_code = await runner.process.wait()
        if runner.observation.state is RuntimeGenerationState.exited:
            return
        reason = runner.selected_exit_reason
        if reason is None:
            reason = RuntimeExitReason.unexpected
            self._record_diagnostic(
                runner.generation_id,
                "runtime_unexpected_exit",
                "The runtime Runner exited outside a requested shutdown.",
            )
        runner.observation = runner.observation.model_copy(
            update={
                "state": RuntimeGenerationState.exited,
                "updated_at": _now(),
                "exit_reason": reason,
                "return_code": return_code,
            }
        )
        self._publish(runner)

    async def _finish_reader(self, runner: _RunnerGeneration) -> None:
        reader = runner.reader
        if reader is None or reader is asyncio.current_task():
            return
        if not reader.done():
            reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, ControlProtocolError, OSError):
            await reader

    async def _finish_watcher(self, runner: _RunnerGeneration) -> None:
        watcher = runner.watcher
        if watcher is None or watcher is asyncio.current_task():
            return
        with contextlib.suppress(asyncio.CancelledError):
            await watcher

    def _mark_exited(self, runner: _RunnerGeneration, reason: RuntimeExitReason) -> None:
        runner.observation = runner.observation.model_copy(
            update={
                "state": RuntimeGenerationState.exited,
                "updated_at": _now(),
                "exit_reason": runner.selected_exit_reason or reason,
                "return_code": runner.process.returncode,
            }
        )
        self._publish(runner)

    def _transition(
        self,
        runner: _RunnerGeneration,
        state: RuntimeGenerationState,
        *,
        readiness: RuntimeReadiness | None = None,
    ) -> None:
        updates: dict[str, object] = {"state": state, "updated_at": _now()}
        if readiness is not None:
            updates["readiness"] = readiness
        runner.observation = runner.observation.model_copy(update=updates)
        self._publish(runner)

    def _publish(self, runner: _RunnerGeneration) -> None:
        self._retain_observation(runner.observation)

    def _retain_observation(self, observation: RuntimeGenerationObservation) -> None:
        self._observations[observation.generation_id] = observation
        protected = {
            self._active.generation_id if self._active is not None else None,
            self._candidate.generation_id if self._candidate is not None else None,
            *self._draining,
        }
        while len(self._observations) > self._settings.retained_generations:
            removable = [item for item in self._observations.values() if item.generation_id not in protected]
            if not removable:
                break
            oldest = min(removable, key=lambda item: item.started_at)
            self._observations.pop(oldest.generation_id, None)

    def _refresh_return_codes(self) -> None:
        for runner in (self._active, self._candidate, *self._draining.values()):
            if (
                runner is not None
                and runner.process.returncode is not None
                and runner.observation.state is not RuntimeGenerationState.exited
            ):
                self._mark_exited(runner, RuntimeExitReason.unexpected)

    def _record_diagnostic(self, generation_id: str | None, code: str, detail: str) -> None:
        self._diagnostics.append(
            RuntimeDiagnostic(
                generation_id=generation_id,
                code=code,
                detail=detail,
                observed_at=_now(),
            )
        )
        del self._diagnostics[: -self._settings.retained_diagnostics]

    @staticmethod
    def _error(
        code: str,
        message: str,
        *,
        generation_id: str | None = None,
    ) -> RuntimeGenerationError:
        details = {"generation_id": generation_id} if generation_id is not None else None
        return RuntimeGenerationError(message, code=code, details=details)


def _remove_execution(
    runner: _RunnerGeneration,
    request_id: str,
    execution: _HostExecution,
) -> None:
    if not execution.result.cancelled():
        execution.result.exception()
    if runner.executions.get(request_id) is execution:
        runner.executions.pop(request_id, None)


def _remove_environment_command(
    runner: _RunnerGeneration,
    request_id: str,
    command: _HostEnvironmentCommand,
) -> None:
    if not command.result.cancelled():
        command.result.exception()
    if runner.environment_commands.get(request_id) is command:
        runner.environment_commands.pop(request_id, None)


async def _complete_cleanup(awaitable: Coroutine[Any, Any, None]) -> bool:
    """Finish one internally bounded cleanup before preserving cancellation."""

    task = asyncio.create_task(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    await task
    return cancelled


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = ["RuntimeGenerationService"]
