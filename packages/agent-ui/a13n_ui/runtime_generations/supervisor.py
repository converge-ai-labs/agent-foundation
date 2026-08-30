"""Serialized fresh-process lifecycle for Agent UI runtime Runners."""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import sys
from collections.abc import Coroutine, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from a13n_ui.errors import RuntimeGenerationError
from a13n_ui.runtime_settings import RuntimeGenerationSettings

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


@dataclass(slots=True)
class _RunnerGeneration:
    generation_id: str
    process: asyncio.subprocess.Process
    channel: ControlChannel
    observation: RuntimeGenerationObservation
    watcher: asyncio.Task[None] | None = None
    expected_exit: bool = False
    selected_exit_reason: RuntimeExitReason | None = None


class RuntimeGenerationService:
    """Own one active Runner and one bounded candidate during restart."""

    def __init__(
        self,
        settings: RuntimeGenerationSettings,
        *,
        command: Sequence[str] | None = None,
        environment: dict[str, str] | None = None,
    ) -> None:
        self._settings = settings
        self._command = tuple(command or (sys.executable, "-m", "a13n_ui.runtime_runner"))
        if not self._command or any(not isinstance(item, str) or not item for item in self._command):
            raise ValueError("runtime Runner command must be a non-empty exact argument vector")
        self._environment = dict(environment or {})
        self._lock = asyncio.Lock()
        self._active: _RunnerGeneration | None = None
        self._candidate: _RunnerGeneration | None = None
        self._observations: dict[str, RuntimeGenerationObservation] = {}
        self._diagnostics: list[RuntimeDiagnostic] = []
        self._closed = False

    async def start(self) -> RuntimeGenerationObservation:
        """Start and promote the initial passive runtime Runner."""

        async with self._lock:
            if self._closed:
                raise self._error("runtime_service_closed", "Runtime supervision is closed.")
            if self._active is not None:
                return self._active.observation
            candidate = await self._start_candidate()
            try:
                await self._prepare_and_activate(candidate)
            except BaseException:
                await _complete_cleanup(self._abort_candidate(candidate))
                raise
            self._active = candidate
            self._candidate = None
            return candidate.observation

    async def restart(self) -> RuntimeRestartResult:
        """Promote a fresh Runner before draining the previously selected generation."""

        async with self._lock:
            if self._closed:
                raise self._error("runtime_service_closed", "Runtime supervision is closed.")
            previous = self._active
            if previous is None:
                candidate = await self._start_candidate()
                try:
                    await self._prepare_and_activate(candidate)
                except BaseException:
                    await _complete_cleanup(self._abort_candidate(candidate))
                    raise
                self._active = candidate
                self._candidate = None
                return RuntimeRestartResult(previous_generation_id=None, active=candidate.observation)

            candidate = await self._start_candidate()
            try:
                await self._prepare_and_activate(candidate)
            except BaseException as exc:
                await _complete_cleanup(self._abort_candidate(candidate))
                if isinstance(exc, asyncio.CancelledError):
                    raise
                if isinstance(exc, RuntimeGenerationError):
                    raise
                raise self._error(
                    "runtime_candidate_failed",
                    "The candidate runtime Runner failed before promotion.",
                    generation_id=candidate.generation_id,
                ) from exc

            self._active = candidate
            self._candidate = None
            cancelled = await _complete_cleanup(self._drain_and_stop(previous))
            if cancelled:
                raise asyncio.CancelledError
            return RuntimeRestartResult(
                previous_generation_id=previous.generation_id,
                active=candidate.observation,
            )

    async def status(self) -> RuntimeStatus:
        """Return detached current routing and bounded retained observations."""

        async with self._lock:
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
                if self._candidate is not None
                and self._candidate.observation.state is not RuntimeGenerationState.exited
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

        async with self._lock:
            if self._closed:
                return
            candidate = self._candidate
            active = self._active
            cancelled = await _complete_cleanup(self._close_generations(candidate, active))
            self._candidate = None
            self._active = None
            self._closed = True
            if cancelled:
                raise asyncio.CancelledError

    async def _close_generations(
        self,
        candidate: _RunnerGeneration | None,
        active: _RunnerGeneration | None,
    ) -> None:
        if candidate is not None:
            await self._abort_candidate(candidate)
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
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command,
                env=environment,
                cwd=str(Path.cwd()),
            )
            channel = await self._await_connection(process, accepted)
            ready = await asyncio.wait_for(
                channel.receive(expected_type="READY"), timeout=self._settings.command_timeout_seconds
            )
            require_generation(ready, generation_id)
            observation = observation.model_copy(
                update={
                    "process_id": process.pid,
                    "state": RuntimeGenerationState.ready,
                    "updated_at": _now(),
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

    async def _prepare_and_activate(self, candidate: _RunnerGeneration) -> None:
        try:
            probe = await asyncio.wait_for(
                candidate.channel.request("PROBE", expected_type="RUNTIME_READY"),
                timeout=self._settings.command_timeout_seconds,
            )
            require_generation(probe, candidate.generation_id)
            readiness = RuntimeReadiness(
                protocol_version=require_string(probe, "protocol_version", max_length=32),
                agent_ui_version=require_string(probe, "agent_ui_version", max_length=64),
                python_version=require_string(probe, "python_version", max_length=64),
                loaded_provenance=require_string_list(probe, "loaded_provenance"),
            )
            expected = current_runtime_readiness()
            if readiness != expected:
                raise self._error(
                    "runtime_readiness_mismatch",
                    "The candidate runtime provenance does not match the Host selection.",
                    generation_id=candidate.generation_id,
                )
            self._transition(candidate, RuntimeGenerationState.preparing, readiness=readiness)
            prepared = await asyncio.wait_for(
                candidate.channel.request("PREPARE", expected_type="PREPARED"),
                timeout=self._settings.command_timeout_seconds,
            )
            require_generation(prepared, candidate.generation_id)
            self._transition(candidate, RuntimeGenerationState.prepared)
            active = await asyncio.wait_for(
                candidate.channel.request("COMMIT", expected_type="ACTIVE"),
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

    async def _abort_candidate(self, candidate: _RunnerGeneration) -> None:
        candidate.expected_exit = True
        candidate.selected_exit_reason = RuntimeExitReason.aborted
        if candidate.process.returncode is None:
            with contextlib.suppress(Exception):
                aborted = await asyncio.wait_for(
                    candidate.channel.request("ABORT", expected_type="ABORTED"),
                    timeout=self._settings.command_timeout_seconds,
                )
                require_generation(aborted, candidate.generation_id)
        await self._ensure_stopped(candidate, RuntimeExitReason.aborted)
        if self._candidate is candidate:
            self._candidate = None

    async def _drain_and_stop(self, runner: _RunnerGeneration) -> None:
        runner.expected_exit = True
        if runner.process.returncode is not None:
            await self._finish_watcher(runner)
            if runner.observation.state is not RuntimeGenerationState.exited:
                self._mark_exited(runner, RuntimeExitReason.unexpected)
            await runner.channel.close()
            return
        if runner.observation.state is RuntimeGenerationState.exited:
            await runner.channel.close()
            await self._finish_watcher(runner)
            return
        runner.selected_exit_reason = RuntimeExitReason.graceful
        if runner.process.returncode is None and runner.observation.state is RuntimeGenerationState.active:
            self._transition(runner, RuntimeGenerationState.draining)
            try:
                drained = await asyncio.wait_for(
                    runner.channel.request("DRAIN", expected_type="DRAINED"),
                    timeout=self._settings.drain_timeout_seconds,
                )
                require_generation(drained, runner.generation_id)
                exiting = await asyncio.wait_for(
                    runner.channel.request("SHUTDOWN", expected_type="EXITING"),
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
        }
        while len(self._observations) > self._settings.retained_generations:
            removable = [item for item in self._observations.values() if item.generation_id not in protected]
            if not removable:
                break
            oldest = min(removable, key=lambda item: item.started_at)
            self._observations.pop(oldest.generation_id, None)

    def _refresh_return_codes(self) -> None:
        for runner in (self._active, self._candidate):
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
