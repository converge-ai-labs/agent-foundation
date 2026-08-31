"""Active Agent UI runtime Runner process implementation."""

from __future__ import annotations

import asyncio
import json
import os
import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from a13n_environment_provider import build_environment_provider_factory_catalog
from pydantic import ValidationError

from a13n_ui.configuration import ConfigurationSettings
from a13n_ui.environments import ProviderRuntimeResolver
from a13n_ui.settings import EnvdRuntimeSettings, StorageSettings
from a13n_ui.storage.layout import StorageLayout

from .execution import RunnerExecutionService
from .models import RuntimeReadiness
from .object_loader import RunnerObjectLoader
from .protocol import ControlChannel, ControlProtocolError, require_generation, require_string
from .wire import (
    ExecuteEnvironmentCommand,
    ExecuteRootRun,
    ProviderStateUpdate,
    RunnerAsyncWorkEvent,
    RunnerContinuationCandidate,
    RunnerRunResult,
)

_ENV_HOST = "A13N_UI_RUNNER_CONTROL_HOST"
_ENV_PORT = "A13N_UI_RUNNER_CONTROL_PORT"
_ENV_TOKEN = "A13N_UI_RUNNER_CONTROL_TOKEN"
_ENV_GENERATION = "A13N_UI_RUNNER_GENERATION_ID"
_ENV_MAX_MESSAGE = "A13N_UI_RUNNER_MAX_MESSAGE_BYTES"
_ENV_STORAGE = "A13N_UI_RUNNER_STORAGE"
_ENV_CONFIGURATION = "A13N_UI_RUNNER_CONFIGURATION"
_ENV_ENVD_RUNTIME = "A13N_UI_RUNNER_ENVD_RUNTIME"


def current_runtime_readiness() -> RuntimeReadiness:
    """Describe the Runner code loaded in this interpreter."""

    names = ("a13n-ui", "a13n-harness", "a13n-stream-protocol", "a13n-environment-provider")
    provenance: list[str] = []
    for name in names:
        try:
            provenance.append(f"{name}=={version(name)}")
        except PackageNotFoundError:
            provenance.append(f"{name}==unavailable")
    return RuntimeReadiness(
        protocol_version="1",
        agent_ui_version=version("a13n-ui"),
        python_version=platform.python_version(),
        loaded_provenance=tuple(provenance),
    )


async def run_runtime_runner() -> int:
    """Authenticate to the parent and serve lifecycle and execution messages."""

    try:
        host = os.environ.pop(_ENV_HOST)
        port = int(os.environ.pop(_ENV_PORT))
        token = os.environ.pop(_ENV_TOKEN)
        generation_id = os.environ.pop(_ENV_GENERATION)
        max_message_bytes = int(os.environ.pop(_ENV_MAX_MESSAGE))
        storage_raw = os.environ.pop(_ENV_STORAGE, "")
        configuration_raw = os.environ.pop(_ENV_CONFIGURATION, "")
        envd_runtime_raw = os.environ.pop(_ENV_ENVD_RUNTIME, "")
    except (KeyError, ValueError):
        return 2

    try:
        reader, writer = await asyncio.open_connection(host, port, limit=max_message_bytes)
        channel = ControlChannel(reader, writer, max_message_bytes=max_message_bytes)
        await channel.send("HELLO", token=token, generation_id=generation_id)
        token = ""
        welcome = await channel.receive(expected_type="WELCOME")
        require_generation(welcome, generation_id)
        readiness = current_runtime_readiness()
        await channel.send("READY", generation_id=generation_id, **readiness.model_dump(mode="json"))
        executor: RunnerExecutionService | None = None
        state = "ready"
        executions: dict[str, asyncio.Task[None]] = {}
        state_acks: dict[str, asyncio.Future[None]] = {}
        drain_task: asyncio.Task[None] | None = None

        async def emit_events(request_id: str, run_id: str, events: tuple[Any, ...]) -> None:
            await channel.send(
                "RUN_EVENT",
                generation_id=generation_id,
                request_id=request_id,
                run_id=run_id,
                events=events,
            )

        async def notify_async_work(event: RunnerAsyncWorkEvent) -> None:
            await channel.send(
                "ASYNC_WORK_EVENT",
                generation_id=generation_id,
                event=event.model_dump(mode="json"),
            )

        executor = _build_executor(
            generation_id,
            storage_raw,
            configuration_raw,
            envd_runtime_raw,
            notify_async_work=notify_async_work,
        )

        async def persist_state(update: ProviderStateUpdate) -> None:
            future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            state_acks[update.update_id] = future
            try:
                await channel.send(
                    "PROVIDER_STATE_UPDATE",
                    generation_id=generation_id,
                    update=update.model_dump(mode="json"),
                )
                await future
            finally:
                state_acks.pop(update.update_id, None)

        async def execute(request: ExecuteRootRun) -> None:
            try:
                try:
                    if executor is None:
                        raise ControlProtocolError("Runner execution storage is not configured")
                    result = await executor.execute(
                        request,
                        emit_events=emit_events,
                        persist_state=persist_state,
                    )
                except BaseException as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        result = RunnerRunResult(
                            request_id=request.request_id,
                            candidate=RunnerContinuationCandidate(
                                run_id=request.request_id,
                                status="cancelled",
                            ),
                        )
                    else:
                        result = RunnerRunResult(
                            request_id=request.request_id,
                            failure={
                                "code": "runner_execution_failed",
                                "message": str(exc) or exc.__class__.__name__,
                            },
                        )
                await channel.send(
                    "RUN_RESULT",
                    generation_id=generation_id,
                    result=result.model_dump(mode="json"),
                )
                await channel.send(
                    "RUN_SCOPE_CLOSED",
                    generation_id=generation_id,
                    request_id=request.request_id,
                )
            finally:
                executions.pop(request.request_id, None)

        async def execute_environment(request: ExecuteEnvironmentCommand) -> None:
            try:
                if executor is None:
                    raise ControlProtocolError("Runner execution storage is not configured")
                result = await executor.execute_environment(request, persist_state=persist_state)
                await channel.send(
                    "ENVIRONMENT_RESULT",
                    generation_id=generation_id,
                    result=result.model_dump(mode="json"),
                )
            except BaseException as exc:
                failure = {"code": "environment_operation_failed", "message": str(exc) or exc.__class__.__name__}
                await channel.send(
                    "ENVIRONMENT_RESULT",
                    generation_id=generation_id,
                    result={"request_id": request.request_id, "failure": failure},
                )
            finally:
                executions.pop(request.request_id, None)

        async def finish_drain() -> None:
            if executions:
                await asyncio.gather(*tuple(executions.values()), return_exceptions=True)
            if executor is not None:
                await executor.wait_idle()
            if state == "draining":
                await channel.send("DRAINED", generation_id=generation_id)

        while True:
            message = await channel.receive()
            message_type = require_string(message, "type", max_length=64)
            if message_type == "ACTIVATE" and state == "ready":
                state = "active"
                await channel.send("ACTIVE", generation_id=generation_id)
            elif message_type == "EXECUTE_ROOT" and state == "active":
                request = ExecuteRootRun.model_validate_json(json.dumps(message.get("request")), strict=True)
                if request.request_id in executions:
                    raise ControlProtocolError("duplicate execution request")
                task = asyncio.create_task(execute(request), name=f"execute-{request.request_id}")
                executions[request.request_id] = task
            elif message_type == "EXECUTE_ENVIRONMENT" and state == "active":
                request = ExecuteEnvironmentCommand.model_validate_json(json.dumps(message.get("request")), strict=True)
                if request.request_id in executions:
                    raise ControlProtocolError("duplicate execution request")
                task = asyncio.create_task(execute_environment(request), name=f"environment-{request.request_id}")
                executions[request.request_id] = task
            elif message_type == "CANCEL_ROOT" and state in {"active", "draining"}:
                request_id = require_string(message, "request_id", max_length=64)
                task = executions.get(request_id)
                accepted = (
                    executor.cancel(request_id, admitted=task is not None and not task.done())
                    if executor is not None
                    else False
                )
                await channel.send(
                    "CANCEL_ROOT_RESULT",
                    generation_id=generation_id,
                    request_id=request_id,
                    accepted=accepted,
                )
            elif message_type in {"PROVIDER_STATE_ACK", "PROVIDER_STATE_FAILED"}:
                update_id = require_string(message, "update_id", max_length=64)
                future = state_acks.get(update_id)
                if future is None or future.done():
                    raise ControlProtocolError("provider state acknowledgement is not pending")
                if message_type == "PROVIDER_STATE_ACK":
                    future.set_result(None)
                else:
                    future.set_exception(ControlProtocolError("Host rejected provider state persistence"))
            elif message_type == "CLOSE_SESSION_WORK" and state == "active":
                request_id = require_string(message, "request_id", max_length=64)
                session_id = require_string(message, "session_id", max_length=128)
                failure: dict[str, str] | None = None
                try:
                    if executor is None:
                        raise ControlProtocolError("Runner execution storage is not configured")
                    await executor.force_close_session_work(session_id)
                except Exception as exc:
                    failure = {
                        "code": "session_work_cleanup_failed",
                        "message": str(exc) or exc.__class__.__name__,
                    }
                await channel.send(
                    "SESSION_WORK_CLOSED",
                    generation_id=generation_id,
                    request_id=request_id,
                    failure=failure,
                )
            elif message_type == "DRAIN" and state == "active":
                state = "draining"
                drain_task = asyncio.create_task(finish_drain(), name=f"drain-{generation_id}")
            elif message_type == "FORCE_CLOSE" and state == "draining":
                state = "force_closing"
                if executor is not None:
                    await executor.close()
                if drain_task is not None:
                    await drain_task
                state = "force_closed"
                await channel.send("FORCE_CLOSED", generation_id=generation_id)
            elif message_type == "SHUTDOWN" and state in {"ready", "draining", "force_closed"} and not executions:
                if drain_task is not None:
                    await drain_task
                if executor is not None:
                    await executor.close()
                await channel.send("EXITING", generation_id=generation_id)
                await channel.close()
                return 0
            else:
                await channel.send("FAILED", code="invalid_transition", generation_id=generation_id)
                await channel.close()
                return 3
    except (ConnectionError, OSError, ControlProtocolError, ValidationError, ValueError):
        return 4


def _build_executor(
    generation_id: str,
    storage_raw: str,
    configuration_raw: str,
    envd_runtime_raw: str,
    *,
    notify_async_work,
) -> RunnerExecutionService | None:
    if not storage_raw or not configuration_raw or not envd_runtime_raw:
        return None
    storage_value = json.loads(storage_raw)
    if isinstance(storage_value, dict) and isinstance(storage_value.get("data_root"), str):
        storage_value["data_root"] = Path(storage_value["data_root"])
    storage = StorageSettings.model_validate(storage_value, strict=True)
    configuration = ConfigurationSettings.model_validate_json(configuration_raw, strict=True)
    envd_runtime = EnvdRuntimeSettings.model_validate_json(envd_runtime_raw, strict=True)
    factories = build_environment_provider_factory_catalog(
        builtin_keys=configuration.builtin_provider_keys,
        extension_keys=configuration.extension_provider_keys,
    )
    return RunnerExecutionService(
        generation_id=generation_id,
        objects=RunnerObjectLoader(storage),
        provider_factories=factories,
        provider_runtimes=ProviderRuntimeResolver(
            layout=StorageLayout.from_root(storage.data_root),
            settings=envd_runtime,
            executable_override=configuration.envd_executable_override,
        ),
        notify_async_work=notify_async_work,
    )


def main() -> None:
    raise SystemExit(asyncio.run(run_runtime_runner()))


if __name__ == "__main__":
    main()


run_passive_runtime_runner = run_runtime_runner

__all__ = ["current_runtime_readiness", "main", "run_passive_runtime_runner", "run_runtime_runner"]
