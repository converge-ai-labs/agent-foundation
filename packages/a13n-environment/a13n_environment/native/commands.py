"""Bounded foreground shell and file helpers over each provider's native exec."""

import asyncio
import base64
import itertools
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from importlib.resources import files
from pathlib import PurePosixPath
from typing import BinaryIO

from pydantic import JsonValue, TypeAdapter

from .._guest_commands import file_helper_source
from ..commands import CommandRequest, ProcessOutputSnapshot, ProcessStatus, ShellCommand, ShellExecResult
from ..models import EnvironmentError, EnvironmentOperationReceipt
from ..retention import EnvironmentOutputCapture
from .configuration import CommandConfiguration

# Native exec returns the bounded stdout of one helper, with no retry after dispatch.
type Execute = Callable[[list[str], float], Awaitable[str]]
_OBJECT = TypeAdapter(dict[str, JsonValue])


class NativeCommands:
    def __init__(self, execute: Execute, configuration: CommandConfiguration, generation: str, execution_id: str):
        self.execute = execute
        self.configuration = configuration
        self.generation = generation
        self.execution_id = execution_id
        self.closed = False
        self._counter = itertools.count(1)
        self._files = file_helper_source()
        self._shell = files("a13n_environment").joinpath("_guest", "command.py").read_text()

    async def helper(self, source: str, arguments: Mapping[str, object]) -> dict[str, JsonValue]:
        if self.closed:
            raise EnvironmentError("Native operations are closed.", code="environment_closed")
        result = await self.execute(
            [self.configuration.python, "-I", "-c", source, json.dumps(arguments)],
            self.configuration.request_timeout_seconds + 10,
        )
        try:
            value = _OBJECT.validate_json(result)
        except ValueError:
            raise EnvironmentError("Invalid native helper response.", code="environment_provider_failure") from None
        if isinstance(error := value.get("error"), str):
            raise EnvironmentError("Native file operation failed.", code=error)
        return value

    async def files(
        self, action: str, arguments: dict[str, JsonValue], *, mutation: bool = False
    ) -> dict[str, JsonValue]:
        del mutation
        return await self.helper(
            self._files,
            {"configuration": self.configuration.model_dump(mode="json"), "action": action, "arguments": arguments},
        )

    async def read_stream(self, path: str) -> AsyncIterator[bytes]:
        offset = 0
        while True:
            value = await self.helper(
                "import base64,json,sys\nr=json.loads(sys.argv[1])\nwith open(r['path'],'rb') as f:\n f.seek(r['offset'])\n print(json.dumps({'data':base64.b64encode(f.read(65536)).decode()}))",
                {"path": path, "offset": offset},
            )
            from .._guest_commands import decoded_bytes

            chunk = decoded_bytes(value)
            if not chunk:
                return
            offset += len(chunk)
            yield chunk

    async def write_stream(self, path: str, source: BinaryIO) -> None:
        offset = 0
        while chunk := await asyncio.to_thread(source.read, 32768):
            await self.helper(
                "import base64,json,sys\nr=json.loads(sys.argv[1])\nwith open(r['path'],'r+b') as f:\n f.seek(r['offset'])\n f.write(base64.b64decode(r['data']))\nprint('{}')",
                {"path": path, "offset": offset, "data": base64.b64encode(chunk).decode()},
            )
            offset += len(chunk)

    def receipt(self) -> EnvironmentOperationReceipt:
        return EnvironmentOperationReceipt(
            execution_id=self.execution_id,
            observed_generation=self.generation,
            operation_id=f"operation-{next(self._counter)}",
            stage="completed",
            outcome="succeeded",
        )

    async def exec(self, request: CommandRequest) -> ShellExecResult:
        config = self.configuration
        unsupported = (
            request.network != "configured"
            or request.initial_stdin is not None
            or request.keep_stdin_open
            or request.output_policy.overflow == "retain"
            or any(
                value is not None for key, value in request.limits.model_dump().items() if key != "wall_time_seconds"
            )
        )
        if unsupported:
            raise EnvironmentError("Requested command guarantee is unsupported.", code="environment_unsupported")
        command = request.command
        if isinstance(command, ShellCommand):
            if command.profile_id != "default":
                raise EnvironmentError("Unknown shell profile.", code="environment_unsupported")
            argv = [config.shell, "-lc" if command.login else "-c", command.script]
        else:
            argv = [command.executable, *command.arguments]
        cwd = str(PurePosixPath(config.root) / (request.cwd or "").lstrip("/"))
        timeout = min(
            request.limits.wall_time_seconds or config.request_timeout_seconds, config.request_timeout_seconds
        )
        limit = min(
            request.output_policy.max_inline_bytes, request.output_policy.max_output_bytes, config.max_output_bytes
        )
        value = await self.helper(
            self._shell,
            {
                "argv": argv,
                "cwd": cwd,
                "env": dict(request.environment.set),
                "unset": list(request.environment.unset),
                "timeout": timeout,
                "limit": limit,
            },
        )
        try:
            stdout = base64.b64decode(str(value["stdout"]), validate=True)
            stderr = base64.b64decode(str(value["stderr"]), validate=True)
            produced = TypeAdapter(tuple[int, int]).validate_python(value["produced"])
            exit_code = TypeAdapter(int).validate_python(value["exit_code"])
            timed_out = TypeAdapter(bool).validate_python(value["timed_out"])
        except (ValueError, KeyError):
            raise EnvironmentError("Invalid command response.", code="environment_provider_failure") from None
        if request.output_policy.overflow == "fail" and any(n > limit for n in produced):
            raise EnvironmentError("Command output exceeded its bound.", code="environment_output_limit")
        captures = tuple(
            _capture(data, count, complete=not timed_out)
            for data, count in zip((stdout, stderr), produced, strict=True)
        )
        status = ProcessStatus(
            phase="timed_out" if timed_out else "exited",
            exit_code=exit_code,
            termination_reason="timeout" if timed_out else "exit",
            cleanup="complete" if value.get("cleanup") == "complete" else "failed",
        )
        receipt = self.receipt()
        if timed_out:
            receipt = receipt.model_copy(update={"outcome": "timed_out"})
        return ShellExecResult(
            status=status, output=ProcessOutputSnapshot(stdout=captures[0], stderr=captures[1]), receipt=receipt
        )


def _capture(data: bytes, produced: int, *, complete: bool) -> EnvironmentOutputCapture:
    truncated = produced > len(data)
    return EnvironmentOutputCapture(
        kind="truncated" if truncated else "inline" if data else "empty",
        coverage="complete" if complete else "partial",
        observation_closed=True,
        producer_complete=complete,
        content_complete=complete and not truncated,
        produced_bytes=produced if complete else None,
        captured_bytes=len(data),
        dropped_bytes=produced - len(data) if complete else None,
        inline=data,
        available_end=len(data),
    )
