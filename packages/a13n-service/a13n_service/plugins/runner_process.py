"""Internal lock-scoped Runner child process boundary."""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from .runner_bootstrap import PluginRunnerBootstrapError, bootstrap_materialized_runtime
from .runner_protocol import (
    PluginRunnerProtocolError,
    read_runner_message,
    require_message_fields,
    write_runner_message,
)

_CONTROL_HOST = "A13N_SERVICE_RUNNER_CONTROL_HOST"
_CONTROL_PORT = "A13N_SERVICE_RUNNER_CONTROL_PORT"
_CONTROL_TOKEN = "A13N_SERVICE_RUNNER_CONTROL_TOKEN"
_GENERATION = "A13N_SERVICE_RUNNER_GENERATION"


async def run_runner_process(runtime_root: Path, runtime_lock_digest: str) -> None:
    host, port, token, generation = _consume_control_environment()
    execution_settings = os.environ.pop("A13N_SERVICE_RUNNER_EXECUTION_SETTINGS", "")
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
        try:
            runtime = bootstrap_materialized_runtime(runtime_root, expected_digest=runtime_lock_digest)
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
        await write_runner_message(
            writer,
            "READY",
            generation=generation,
            runtime_lock_digest=runtime_lock_digest,
            provenance=provenance,
        )
        async with AsyncExitStack() as worker_scope:
            worker = None
            while True:
                command = await read_runner_message(reader)
                command_type = command.get("type")
                if command_type == "ACTIVATE":
                    runtime_version = command.get("runtime_version")
                    if not isinstance(runtime_version, int) or isinstance(runtime_version, bool) or runtime_version < 0:
                        raise PluginRunnerProtocolError("Runner activation version is invalid")
                    if execution_settings and worker is None:
                        from a13n_service.process.runner import open_runner_worker
                        from a13n_service.settings import Settings

                        worker = await worker_scope.enter_async_context(
                            open_runner_worker(Settings.model_validate_json(execution_settings), runtime)
                        )
                    await write_runner_message(
                        writer,
                        "ACTIVE",
                        generation=generation,
                        runtime_lock_digest=runtime_lock_digest,
                        runtime_version=runtime_version,
                    )
                elif command_type == "DRAIN":
                    if worker is not None and worker.execution_loop is not None:
                        from a13n_service.interactions.domain import RunAttemptYieldReason

                        await worker.execution_loop.drain(
                            RunAttemptYieldReason(command.get("reason", "runner_rotation"))
                        )
                    await worker_scope.aclose()
                    worker = None
                    await write_runner_message(writer, "DRAINED", generation=generation)
                elif command_type == "SHUTDOWN":
                    await write_runner_message(writer, "EXITING", generation=generation)
                    return
                else:
                    raise PluginRunnerProtocolError("Runner control command is unsupported")
    finally:
        writer.close()
        await writer.wait_closed()


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
        asyncio.run(run_runner_process(Path(sys.argv[1]), sys.argv[2]))
    except (PluginRunnerBootstrapError, PluginRunnerProtocolError):
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
