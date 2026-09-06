"""Internal lock-scoped Runner child process boundary."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import traceback
from pathlib import Path

from a13n_logging import get_logger
from anyio import to_thread
from pydantic import ValidationError

from a13n_service.interactions.domain import RunAttemptYieldReason
from a13n_service.interactions.worker import WorkerIdentity
from a13n_service.log import configure_logging
from a13n_service.process.build import worker_build_id
from a13n_service.process.runner import open_runner_execution
from a13n_service.settings import Settings

from .runner_bootstrap import PluginRunnerBootstrapError, bootstrap_materialized_runtime
from .runner_protocol import (
    PluginRunnerProtocolError,
    read_runner_message,
    require_message_fields,
    write_runner_message,
)

_CONTROL_HOST = "FOUNDATION_RUNNER_CONTROL_HOST"
_CONTROL_PORT = "FOUNDATION_RUNNER_CONTROL_PORT"
_CONTROL_TOKEN = "FOUNDATION_RUNNER_CONTROL_TOKEN"
_GENERATION = "FOUNDATION_RUNNER_GENERATION"


async def run_runner_process(runtime_root: Path, runtime_lock_digest: str) -> None:
    host, port, token, generation = _consume_control_environment()
    try:
        reader, writer = await asyncio.open_connection(host, port, limit=64 * 1024 + 1)
    except (OSError, ValueError) as error:
        raise PluginRunnerProtocolError("Runner control connection failed") from error
    try:
        await write_runner_message(
            writer,
            "HELLO",
            token=token,
            generation=generation,
            runtime_lock_digest=runtime_lock_digest,
        )
        welcome = await read_runner_message(reader)
        require_message_fields(welcome, message_type="WELCOME", string_fields=("generation",))
        if welcome["generation"] != generation:
            raise PluginRunnerProtocolError("Runner generation handshake changed")
        payload = welcome.get("settings")
        worker_id = welcome.get("worker_id")
        if (
            not isinstance(payload, dict)
            or set(payload) != set(Settings.model_fields)
            or not isinstance(worker_id, str)
            or not worker_id
        ):
            raise PluginRunnerProtocolError("Runner execution settings are missing")
        try:
            settings = Settings(**{"_env_file": None, **payload})
        except ValidationError:
            raise PluginRunnerProtocolError("Runner execution settings are invalid") from None
        if settings.role != "worker" or settings.plugin_runtime_mode != "runner" or settings.auto_migrate:
            raise PluginRunnerProtocolError("Runner execution role is invalid")
        configure_logging(settings)
        # Resolve the service artifact before adding any Plugin distribution to sys.path.
        build_id = await to_thread.run_sync(worker_build_id)
        try:
            runtime = await to_thread.run_sync(
                lambda: bootstrap_materialized_runtime(runtime_root, expected_digest=runtime_lock_digest)
            )
        except PluginRunnerBootstrapError as error:
            await write_runner_message(writer, "FAILED", code=error.reason)
            raise
        provenance = [
            {
                "plugin_key": item.plugin_key,
                "distribution_name": item.distribution_name,
                "distribution_version": item.version,
                "wheel_digest": item.wheel_digest,
            }
            for item in runtime.runtime_lock.plugins
        ]
        async with open_runner_execution(
            settings, runtime, WorkerIdentity(worker_id, generation, build_id)
        ) as execution:
            await write_runner_message(
                writer,
                "READY",
                generation=generation,
                runtime_lock_digest=runtime_lock_digest,
                provenance=provenance,
                worker_build_id=build_id,
            )
            while True:
                command = await read_runner_message(reader)
                command_type = command.get("type")
                if command_type == "ACTIVATE":
                    runtime_version = command.get("runtime_version")
                    if not isinstance(runtime_version, int) or isinstance(runtime_version, bool) or runtime_version < 0:
                        raise PluginRunnerProtocolError("Runner activation version is invalid")
                    execution.loop.enable_claims()
                    await write_runner_message(
                        writer,
                        "ACTIVE",
                        generation=generation,
                        runtime_lock_digest=runtime_lock_digest,
                        runtime_version=runtime_version,
                    )
                elif command_type == "START":
                    execution.loop.enable_claims()
                    await write_runner_message(writer, "STARTED", generation=generation)
                elif command_type == "STATUS":
                    await write_runner_message(
                        writer,
                        "STATUS",
                        generation=generation,
                        active_count=execution.loop.active_count,
                        ready=execution.loop.ready,
                    )
                elif command_type == "RETIRE":
                    # Capacity includes in-flight claims. No await separates this check
                    # from gating claims, so retirement cannot race a new reservation.
                    idle = execution.loop.active_count == 0
                    if idle:
                        await execution.drain(RunAttemptYieldReason.runner_rotation)
                    await write_runner_message(writer, "RETIRED", generation=generation, retired=idle)
                    if idle:
                        return
                elif command_type in {"DRAIN", "SHUTDOWN"}:
                    try:
                        reason = RunAttemptYieldReason(command.get("reason", "service_drain"))
                    except ValueError:
                        raise PluginRunnerProtocolError("Runner drain reason is invalid") from None
                    await execution.drain(reason)
                    await write_runner_message(
                        writer,
                        "DRAINED" if command_type == "DRAIN" else "EXITING",
                        generation=generation,
                    )
                    if command_type == "SHUTDOWN":
                        return
                else:
                    raise PluginRunnerProtocolError("Runner control command is unsupported")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


def _consume_control_environment() -> tuple[str, int, str, str]:
    host = os.environ.pop(_CONTROL_HOST, "")
    raw_port = os.environ.pop(_CONTROL_PORT, "")
    token = os.environ.pop(_CONTROL_TOKEN, "")
    generation = os.environ.pop(_GENERATION, "")
    try:
        port = int(raw_port)
    except ValueError as error:
        raise PluginRunnerProtocolError("Runner control environment is invalid") from error
    if host != "127.0.0.1" or not 1 <= port <= 65535 or not token or not generation:
        raise PluginRunnerProtocolError("Runner control environment is invalid")
    if len(token) > 512 or len(generation) > 128:
        raise PluginRunnerProtocolError("Runner control environment exceeds its bound")
    return host, port, token, generation


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(2)
    try:
        asyncio.run(_run_with_signals())
    except (Exception, asyncio.CancelledError) as error:
        # The boundary must not print exception payloads containing credentials or Run content.
        cause = error
        for _ in range(8):
            nested = cause.exceptions[0] if isinstance(cause, BaseExceptionGroup) else cause.__cause__
            if nested is None:
                break
            cause = nested
        get_logger("a13n_service.plugins.runner_process").error(
            "runner_process_failed",
            extra={
                "error_type": type(cause).__name__,
                "context_type": type(cause.__context__).__name__,
                "frames": [(frame.name, frame.lineno) for frame in traceback.extract_tb(cause.__traceback__)[-24:]],
            },
        )
        raise SystemExit(1) from None


async def _run_with_signals() -> None:
    task = asyncio.current_task()
    assert task is not None
    loop = asyncio.get_running_loop()
    stopping = False

    def stop() -> None:
        nonlocal stopping
        stopping = True
        task.cancel()

    loop.add_signal_handler(signal.SIGTERM, stop)
    loop.add_signal_handler(signal.SIGINT, stop)
    try:
        await run_runner_process(Path(sys.argv[1]), sys.argv[2])
    except asyncio.CancelledError:
        if not stopping:
            raise
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
        loop.remove_signal_handler(signal.SIGINT)


if __name__ == "__main__":
    main()
