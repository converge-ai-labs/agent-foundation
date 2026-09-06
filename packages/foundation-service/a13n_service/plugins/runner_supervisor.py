"""Foundation-specific local lifecycle authority for lock-scoped Runner children."""

from __future__ import annotations

import asyncio
import hmac
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass, field

from a13n_harness import SafeFailure
from anyio import CancelScope, to_thread
from packaging.utils import canonicalize_name

from a13n_service.ids import new_object_id
from a13n_service.process.build import worker_build_id
from a13n_service.process.runner_settings import runner_settings_payload
from a13n_service.settings import Settings

from .commands import PluginRuntimeCommandFailure
from .materialization import PluginRuntimeMaterializationError, PluginRuntimeMaterializer
from .runner_protocol import (
    PluginRunnerProtocolError,
    read_runner_message,
    require_message_fields,
    write_runner_message,
)
from .runtime import PluginRuntimeLock


@dataclass(slots=True)
class _RunnerProcess:
    generation: str
    runtime_lock_digest: str
    process: asyncio.subprocess.Process
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    listener: asyncio.Server
    runtime_lock: PluginRuntimeLock
    claims_enabled: bool = False
    active_runtime_version: int | None = None
    command_lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.command_lock = asyncio.Lock()

    async def request(
        self,
        command: str,
        expected_response: str,
        *,
        timeout_seconds: float,
        **fields: object,
    ) -> dict[str, object]:
        async with self.command_lock:
            if self.process.returncode is not None:
                raise PluginRunnerProtocolError("Runner process has exited")
            try:
                async with asyncio.timeout(timeout_seconds):
                    await write_runner_message(self.writer, command, **fields)
                    response = await read_runner_message(self.reader)
            except TimeoutError as error:
                self.writer.close()
                raise PluginRunnerProtocolError("Runner response timed out") from error
            except PluginRunnerProtocolError:
                self.writer.close()
                raise
            try:
                require_message_fields(response, message_type=expected_response, string_fields=("generation",))
                if response["generation"] != self.generation:
                    raise PluginRunnerProtocolError("Runner response generation changed")
            except PluginRunnerProtocolError:
                self.writer.close()
                raise
            return response

    @property
    def available(self) -> bool:
        return self.process.returncode is None and not self.writer.is_closing() and not self.reader.at_eof()


@dataclass(frozen=True, slots=True)
class _StagedOperation:
    runtime_lock_digest: str
    staging_token: str


class PluginRunnerSupervisor:
    """Own fresh local Runner children without importing Plugin code itself."""

    def __init__(
        self,
        materializer: PluginRuntimeMaterializer,
        *,
        settings: Settings,
        ready_timeout_seconds: float = 60,
        command_timeout_seconds: float = 30,
        shutdown_timeout_seconds: float = 30,
        max_processes: int = 8,
    ) -> None:
        if min(ready_timeout_seconds, command_timeout_seconds, shutdown_timeout_seconds) <= 0:
            raise ValueError("Runner Supervisor timeouts must be positive")
        if max_processes <= 0 or max_processes > 256:
            raise ValueError("Runner Supervisor process limit must be between one and 256")
        self._materializer = materializer
        self._settings = settings
        self.worker_id = new_object_id("worker")
        self._build_id: str | None = None
        self._ready_timeout_seconds = ready_timeout_seconds
        self._command_timeout_seconds = command_timeout_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._max_processes = max_processes
        self._runners: dict[str, _RunnerProcess] = {}
        self._operations: dict[str, _StagedOperation] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._draining = False
        self._catalog_active_digest: str | None = None

    @property
    def catalog_active_digest(self) -> str | None:
        return self._catalog_active_digest

    @property
    def runtime_lock_digests(self) -> tuple[str, ...]:
        return tuple(sorted(self._runners))

    async def __aenter__(self) -> PluginRunnerSupervisor:
        self._build_id = await to_thread.run_sync(worker_build_id)
        return self

    @property
    def build_id(self) -> str:
        if self._build_id is None:
            raise RuntimeError("Runner Supervisor has not started")
        return self._build_id

    @property
    def ready(self) -> bool:
        active = self._runners.get(self._catalog_active_digest) if self._catalog_active_digest is not None else None
        return (
            not self._closed
            and not self._draining
            and (self._catalog_active_digest is None or (active is not None and active.claims_enabled))
            and all(runner.available for runner in self._runners.values() if runner.claims_enabled)
        )

    async def ensure_execution(self, runtime_lock: PluginRuntimeLock, *, catalog_active: bool = False) -> None:
        async with self._lock:
            self._require_open()
            if catalog_active:
                self._catalog_active_digest = runtime_lock.digest
            # Staging is not permission to consume historical work either.
            if any(item.runtime_lock_digest == runtime_lock.digest for item in self._operations.values()):
                return
            runner = await self._ensure_runner(runtime_lock)
            self._require_open()
            if not runner.claims_enabled:
                await runner.request("START", "STARTED", timeout_seconds=self._command_timeout_seconds)
                runner.claims_enabled = True

    async def _retire_idle(self) -> None:
        staged = {item.runtime_lock_digest for item in self._operations.values()}
        for digest, runner in tuple(self._runners.items()):
            if digest == self._catalog_active_digest or digest in staged or not runner.claims_enabled:
                continue
            try:
                response = await runner.request("RETIRE", "RETIRED", timeout_seconds=self._drain_timeout)
            except PluginRunnerProtocolError:
                # No new command may assume that a disconnected child is idle.
                continue
            if response.get("retired") is True:
                self._runners.pop(digest)
                await self._stop_runner(runner)
                return

    async def restore_exited(self) -> None:
        async with self._lock:
            self._require_open()
            for old in tuple(self._runners.values()):
                if old.claims_enabled and not old.available:
                    runner = await self._ensure_runner(old.runtime_lock)
                    await runner.request("START", "STARTED", timeout_seconds=self._command_timeout_seconds)
                    runner.claims_enabled = True
                    runner.active_runtime_version = old.active_runtime_version

    async def drain(self) -> None:
        # Gate admission before waiting for a staging command that may be in flight.
        self._draining = True
        async with self._lock:
            async with asyncio.TaskGroup() as tasks:
                for runner in self._runners.values():
                    tasks.create_task(self._drain_runner(runner))

    async def _drain_runner(self, runner: _RunnerProcess) -> None:
        try:
            if runner.process.returncode is None:
                await runner.request(
                    "DRAIN",
                    "DRAINED",
                    timeout_seconds=self._drain_timeout,
                    reason="service_drain",
                )
        except PluginRunnerProtocolError:
            # A dead or unresponsive child cannot release PostgreSQL authority by IPC.
            await self._stop_process(runner.process)

    @property
    def _drain_timeout(self) -> float:
        return (
            self._settings.worker_drain_timeout_seconds
            + self._settings.worker_cleanup_timeout_seconds
            + self._command_timeout_seconds
        )

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        await self.close()

    async def stage_candidate(
        self,
        *,
        operation_id: str,
        runtime_lock: PluginRuntimeLock,
    ) -> str:
        async with self._lock:
            self._require_open()
            staged = self._operations.get(operation_id)
            if staged is not None:
                if staged.runtime_lock_digest != runtime_lock.digest:
                    raise _failure("plugin_runtime_changed", "The staged Plugin Runtime changed.")
                await self._ensure_runner(runtime_lock)
                return staged.staging_token
            await self._ensure_runner(runtime_lock)
            staging_token = secrets.token_urlsafe(32)
            self._operations[operation_id] = _StagedOperation(runtime_lock.digest, staging_token)
            return staging_token

    async def activate_candidate(
        self,
        *,
        operation_id: str,
        runtime_lock: PluginRuntimeLock,
        staging_token: str,
        runtime_generation: int,
    ) -> None:
        async with self._lock:
            self._require_open()
            staged = self._operations.get(operation_id)
            if staged is None:
                await self._ensure_runner(runtime_lock)
                staged = _StagedOperation(runtime_lock.digest, staging_token)
                self._operations[operation_id] = staged
            if staged.runtime_lock_digest != runtime_lock.digest or not hmac.compare_digest(
                staged.staging_token, staging_token
            ):
                raise _failure("plugin_runtime_staging_invalid", "The staged Plugin Runtime is invalid.")
            runner = await self._ensure_runner(runtime_lock)
            if runner.active_runtime_version != runtime_generation:
                self._require_open()
                try:
                    response = await runner.request(
                        "ACTIVATE",
                        "ACTIVE",
                        timeout_seconds=self._command_timeout_seconds,
                        runtime_version=runtime_generation,
                    )
                except PluginRunnerProtocolError as error:
                    raise _failure(
                        "plugin_runtime_staging_unavailable",
                        "The staged Plugin Runtime did not acknowledge activation.",
                        retryable=True,
                    ) from error
                if (
                    response.get("runtime_lock_digest") != runtime_lock.digest
                    or response.get("runtime_version") != runtime_generation
                ):
                    raise _failure(
                        "plugin_runtime_staging_invalid",
                        "The staged Plugin Runtime acknowledged different content.",
                    )
                runner.active_runtime_version = runtime_generation
                runner.claims_enabled = True
            self._catalog_active_digest = runtime_lock.digest
            self._operations.pop(operation_id, None)

    async def abort_candidate(
        self,
        *,
        operation_id: str,
        runtime_lock: PluginRuntimeLock,
        staging_token: str | None,
    ) -> None:
        async with self._lock:
            staged = self._operations.get(operation_id)
            if staged is None:
                return
            if staged.runtime_lock_digest != runtime_lock.digest:
                return
            if staging_token is not None and not hmac.compare_digest(staged.staging_token, staging_token):
                return
            self._operations.pop(operation_id)
            runner = self._runners.get(runtime_lock.digest)
            if runner is None or runner.active_runtime_version is not None or runner.claims_enabled:
                return
            if any(item.runtime_lock_digest == runtime_lock.digest for item in self._operations.values()):
                return
            self._runners.pop(runtime_lock.digest, None)
            await self._stop_runner(runner)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            runners = tuple(self._runners.values())
            self._runners.clear()
            self._operations.clear()
            self._catalog_active_digest = None
            with CancelScope(shield=True):
                async with asyncio.TaskGroup() as tasks:
                    for runner in runners:
                        tasks.create_task(self._stop_runner(runner))

    def _require_open(self) -> None:
        if self._closed or self._draining:
            raise _failure("plugin_runtime_staging_unavailable", "The Plugin Runner Supervisor is closed.")

    async def _ensure_runner(self, runtime_lock: PluginRuntimeLock) -> _RunnerProcess:
        runner = self._runners.get(runtime_lock.digest)
        if runner is not None and runner.available:
            return runner
        if runner is not None:
            self._runners.pop(runtime_lock.digest, None)
            await self._stop_runner(runner)
        return await self._start_runner(runtime_lock)

    async def _start_runner(self, runtime_lock: PluginRuntimeLock) -> _RunnerProcess:
        if len(self._runners) >= self._max_processes:
            await self._retire_idle()
        if len(self._runners) >= self._max_processes:
            raise _failure("plugin_runtime_capacity_exceeded", "Plugin Runner capacity is exhausted.")
        try:
            materialized = await self._materializer.materialize(runtime_lock)
        except PluginRuntimeMaterializationError as error:
            raise _failure("plugin_runtime_incompatible", "The Plugin Runtime cannot be materialized.") from error

        loop = asyncio.get_running_loop()
        connection: asyncio.Future[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = loop.create_future()
        generation = new_object_id("rgen")
        connection_token = secrets.token_urlsafe(48)
        authentication_tasks: set[asyncio.Task[None]] = set()

        async def authenticate(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            accepted = False
            try:
                async with asyncio.timeout(self._ready_timeout_seconds):
                    hello = await read_runner_message(reader)
                token, hello_generation, digest = require_message_fields(
                    hello,
                    message_type="HELLO",
                    string_fields=("token", "generation", "runtime_lock_digest"),
                )
                if (
                    hmac.compare_digest(token, connection_token)
                    and hello_generation == generation
                    and digest == runtime_lock.digest
                    and not connection.done()
                ):
                    connection.set_result((reader, writer))
                    accepted = True
            except (PluginRunnerProtocolError, TimeoutError):
                pass
            finally:
                if not accepted:
                    await _close_writer(writer, timeout_seconds=self._shutdown_timeout_seconds)

        def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.create_task(authenticate(reader, writer))
            authentication_tasks.add(task)
            task.add_done_callback(authentication_tasks.discard)

        server = await asyncio.start_server(connected, "127.0.0.1", 0, limit=64 * 1024 + 1)
        socket = server.sockets[0] if server.sockets else None
        if socket is None:
            server.close()
            await server.wait_closed()
            raise _failure("plugin_runtime_staging_unavailable", "Runner control listener is unavailable.")
        port = int(socket.getsockname()[1])
        environment = {name: value for name, value in os.environ.items() if not name.startswith("FOUNDATION_")}
        environment.update(
            {
                "FOUNDATION_RUNNER_CONTROL_HOST": "127.0.0.1",
                "FOUNDATION_RUNNER_CONTROL_PORT": str(port),
                "FOUNDATION_RUNNER_CONTROL_TOKEN": connection_token,
                "FOUNDATION_RUNNER_GENERATION": generation,
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        process: asyncio.subprocess.Process | None = None
        writer: asyncio.StreamWriter | None = None
        started = False
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "a13n_service.plugins.runner_process",
                str(materialized.root),
                runtime_lock.digest,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=None,
                stderr=None,
                start_new_session=True,
            )
            exit_task = asyncio.create_task(process.wait())
            try:
                done, _pending = await asyncio.wait(
                    {connection, exit_task},
                    timeout=self._ready_timeout_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if connection not in done:
                    raise PluginRunnerProtocolError("Runner exited or timed out before connecting")
                reader, writer = connection.result()
            finally:
                if not exit_task.done():
                    exit_task.cancel()
                    await asyncio.gather(exit_task, return_exceptions=True)
            await write_runner_message(
                writer,
                "WELCOME",
                generation=generation,
                worker_id=self.worker_id,
                settings=runner_settings_payload(self._settings),
            )
            async with asyncio.timeout(self._ready_timeout_seconds):
                ready = await read_runner_message(reader)
            if ready.get("type") == "FAILED":
                raise PluginRunnerProtocolError("Runner bootstrap failed")
            require_message_fields(
                ready,
                message_type="READY",
                string_fields=("generation", "runtime_lock_digest"),
            )
            if (
                ready["generation"] != generation
                or ready["runtime_lock_digest"] != runtime_lock.digest
                or not _provenance_matches(ready.get("provenance"), runtime_lock)
                or ready.get("worker_build_id") != self.build_id
            ):
                raise PluginRunnerProtocolError("Runner readiness provenance changed")
            runner = _RunnerProcess(generation, runtime_lock.digest, process, reader, writer, server, runtime_lock)
            self._runners[runtime_lock.digest] = runner
            started = True
            return runner
        except (OSError, PluginRunnerProtocolError, TimeoutError) as error:
            raise _failure("plugin_runtime_staging_failed", "The Plugin Runner failed readiness validation.") from error
        finally:
            if not started:
                with CancelScope(shield=True):
                    if writer is not None:
                        await _close_writer(writer, timeout_seconds=self._shutdown_timeout_seconds)
                    if process is not None:
                        await self._stop_process(process)
            server.close()
            for task in authentication_tasks:
                task.cancel()
            if authentication_tasks:
                await asyncio.gather(*authentication_tasks, return_exceptions=True)
            if not started:
                try:
                    async with asyncio.timeout(self._shutdown_timeout_seconds):
                        await server.wait_closed()
                except TimeoutError:
                    pass

    async def _stop_runner(self, runner: _RunnerProcess) -> None:
        try:
            if runner.process.returncode is None:
                await runner.request(
                    "SHUTDOWN",
                    "EXITING",
                    timeout_seconds=self._drain_timeout,
                )
        except PluginRunnerProtocolError:
            pass
        finally:
            await _close_writer(runner.writer, timeout_seconds=self._shutdown_timeout_seconds)
            try:
                async with asyncio.timeout(self._shutdown_timeout_seconds):
                    await runner.listener.wait_closed()
            except TimeoutError:
                pass
            await self._stop_process(runner.process)

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            async with asyncio.timeout(self._shutdown_timeout_seconds):
                await process.wait()
            return
        except TimeoutError:
            try:
                process.terminate()
            except ProcessLookupError:
                return
        try:
            async with asyncio.timeout(self._shutdown_timeout_seconds):
                await process.wait()
            return
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                return
            await process.wait()


async def _close_writer(writer: asyncio.StreamWriter, *, timeout_seconds: float) -> None:
    writer.close()
    try:
        async with asyncio.timeout(timeout_seconds):
            await writer.wait_closed()
    except (ConnectionError, OSError, TimeoutError):
        pass


def _provenance_matches(value: object, runtime_lock: PluginRuntimeLock) -> bool:
    if not isinstance(value, list):
        return False
    expected = sorted(
        (
            item.plugin_key,
            str(canonicalize_name(item.distribution_name)),
            item.version,
            item.wheel_digest,
        )
        for item in runtime_lock.plugins
    )
    actual: list[tuple[str, str, str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            return False
        plugin_key = item.get("plugin_key")
        distribution_name = item.get("distribution_name")
        distribution_version = item.get("distribution_version")
        wheel_digest = item.get("wheel_digest")
        if not (
            isinstance(plugin_key, str)
            and isinstance(distribution_name, str)
            and isinstance(distribution_version, str)
            and isinstance(wheel_digest, str)
        ):
            return False
        actual.append(
            (
                plugin_key,
                str(canonicalize_name(distribution_name)),
                distribution_version,
                wheel_digest,
            )
        )
    return sorted(actual) == expected


def _failure(code: str, message: str, *, retryable: bool = False) -> PluginRuntimeCommandFailure:
    return PluginRuntimeCommandFailure(SafeFailure(code=code, message=message), retryable=retryable)
