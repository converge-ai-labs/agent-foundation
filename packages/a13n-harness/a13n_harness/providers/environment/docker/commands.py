"""Bounded one-shot Docker exec helpers; no guest daemon or network transport."""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import BinaryIO

from pydantic import JsonValue, TypeAdapter

from a13n_harness.environment.models import EnvironmentError, EnvironmentOperationReceipt
from a13n_harness.providers.environment._guest_commands import file_helper_source
from a13n_harness.providers.environment.docker.configuration import DockerEnvironmentConfiguration
from a13n_harness.providers.environment.docker.errors import engine_errors
from a13n_harness.providers.environment.docker.observation import attach, disconnect, frames
from a13n_harness.providers.environment.docker.runtime import DockerSDKEngine
from a13n_harness.providers.environment.native.transfers import CHUNK_BYTES, remaining_bytes

_OBJECT = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True)
class DockerFileConfiguration:
    max_file_bytes: int


class DockerCommands:
    def __init__(self, engine: DockerSDKEngine, container_id: str, config: DockerEnvironmentConfiguration) -> None:
        self.closed = False
        self.engine = engine
        self.container_id = container_id
        self.config = config
        self.configuration = DockerFileConfiguration(config.max_file_bytes)
        self.execution_id = "workspace"
        self._operations = itertools.count(1)
        self._source = file_helper_source()

    def receipt(self) -> EnvironmentOperationReceipt:
        return EnvironmentOperationReceipt(
            execution_id=self.execution_id,
            observed_generation=self.container_id,
            operation_id=f"op-{next(self._operations)}",
            stage="completed",
            outcome="succeeded",
        )

    def check_open(self) -> None:
        if self.closed:
            raise EnvironmentError("Docker operation scope is closed", code="environment_unavailable")

    async def execute(self, argv: list[str], *, maximum: int | None = None, mutation: bool = True) -> bytes:
        self.check_open()
        ceiling = maximum or self.config.max_file_bytes * 2 + 65536
        api = self.engine.client.api
        with engine_errors(mutation=mutation):
            created = await asyncio.to_thread(
                api.exec_create, self.container_id, argv, user=self.config.user or "", workdir="/workspace"
            )
            connection = await attach(api, created["Id"])

            def consume() -> bytes:
                result = bytearray()
                for _stream, chunk in frames(connection):
                    if len(result) + len(chunk) > ceiling:
                        raise EnvironmentError("Docker helper output limit exceeded", code="environment_too_large")
                    result.extend(chunk)
                return bytes(result)

            consuming = asyncio.create_task(asyncio.to_thread(consume))
            try:
                async with asyncio.timeout(self.config.request_timeout_seconds):
                    result = await asyncio.shield(consuming)
                    status = await asyncio.to_thread(api.exec_inspect, created["Id"])
                if status["ExitCode"] != 0:
                    raise EnvironmentError("Docker helper failed", code="environment_provider_failure")
                return result
            finally:
                await asyncio.to_thread(disconnect, connection)
                await asyncio.gather(consuming, return_exceptions=True)
                response = getattr(connection, "_response", None)
                await asyncio.to_thread(response.close if response is not None else connection.close)

    async def files(
        self, action: str, arguments: dict[str, JsonValue], *, mutation: bool = False
    ) -> dict[str, JsonValue]:
        request = {
            "configuration": {
                "root": "/",
                "max_file_bytes": self.config.max_file_bytes,
                "max_query_entries": self.config.max_query_entries,
                "request_timeout_seconds": self.config.request_timeout_seconds,
            },
            "action": action,
            "arguments": arguments,
        }
        value = _OBJECT.validate_json(
            await self.execute([self.config.python, "-I", "-c", self._source, json.dumps(request)], mutation=mutation)
        )
        error = value.get("error")
        if isinstance(error, str):
            details = value.get("details")
            raise EnvironmentError(
                "Docker file operation failed", code=error, details=details if isinstance(details, dict) else {}
            )
        return value

    @asynccontextmanager
    async def _transfer(self, script: str, path: str, *, size: int | None = None):
        self.check_open()
        api = self.engine.client.api
        with engine_errors(mutation=size is not None):
            created = await asyncio.to_thread(
                api.exec_create,
                self.container_id,
                [
                    self.config.python,
                    "-I",
                    "-c",
                    script,
                    path,
                    str(self.config.request_timeout_seconds),
                    str(size or 0),
                ],
                user=self.config.user or "",
                workdir="/workspace",
                stdin=size is not None,
            )
            connection = await attach(api, created["Id"])
            try:
                async with asyncio.timeout(self.config.request_timeout_seconds):
                    yield connection
                    status = await asyncio.to_thread(api.exec_inspect, created["Id"])
                    if status["ExitCode"] != 0:
                        raise EnvironmentError("Docker file transfer failed", code="environment_provider_failure")
            finally:
                await asyncio.to_thread(disconnect, connection)
                response = getattr(connection, "_response", None)
                await asyncio.to_thread(response.close if response is not None else connection.close)

    def read_stream(self, path: str) -> AsyncIterator[bytes]:
        return self._read_stream(path)

    async def _read_stream(self, path: str, *, offset: int = 0, length: int | None = None) -> AsyncIterator[bytes]:
        # One configured-user process streams raw stdout; no archive API bypasses
        # guest permissions. The alarm bounds the process after an early detach.
        script = (
            "import hashlib,json,signal,sys; signal.alarm(int(sys.argv[2])); "
            f"f=open(sys.argv[1],'rb'); f.seek({offset}); remaining={length!r}; size=0; digest=hashlib.sha256()\n"
            "while remaining is None or remaining>0:\n"
            " chunk=f.read(65536 if remaining is None else min(65536,remaining))\n"
            " if not chunk: break\n"
            " sys.stdout.buffer.write(chunk); sys.stdout.buffer.flush(); size+=len(chunk); digest.update(chunk)\n"
            " if remaining is not None: remaining-=len(chunk)\n"
            "sys.stderr.write(json.dumps({'size':size,'sha256':digest.hexdigest()})); sys.stderr.flush()\n"
        )
        async with self._transfer(script, path) as connection:
            iterator = frames(connection)
            total = 0
            digest = hashlib.sha256()
            completion = bytearray()
            while True:
                pending = asyncio.create_task(asyncio.to_thread(next, iterator, None))
                try:
                    frame = await asyncio.shield(pending)
                except BaseException:
                    await asyncio.to_thread(disconnect, connection)
                    await asyncio.gather(pending, return_exceptions=True)
                    raise
                if frame is None:
                    break
                stream, chunk = frame
                if stream == 1:
                    total += len(chunk)
                    digest.update(chunk)
                    yield chunk
                else:
                    # stderr carries only a bounded completion record, never file
                    # bytes. Pipe multiplexing may deliver it before final stdout.
                    if len(completion) + len(chunk) > 1024:
                        raise EnvironmentError("Docker file read failed", code="environment_provider_failure")
                    completion.extend(chunk)
            try:
                value = _OBJECT.validate_json(completion)
            except ValueError:
                raise EnvironmentError("Docker file read was incomplete", code="environment_provider_failure") from None
            if type(value.get("size")) is not int or value != {"size": total, "sha256": digest.hexdigest()}:
                raise EnvironmentError("Docker file read integrity check failed", code="environment_provider_failure")

    async def read_range(self, path: str, offset: int, length: int) -> bytes:
        # Docker uses one configured-user process which can seek before sending.
        value = await self.files("resolve", {"path": path, "regular_file": True})
        native = TypeAdapter(str).validate_python(value["path"])
        result = bytearray()
        # The guest already seeks and bounds this stream. Drain through its
        # completion record even when exactly length bytes have been received.
        async for chunk in self._read_stream(native, offset=offset, length=length):
            if len(result) + len(chunk) > length:
                raise EnvironmentError("Docker range exceeded its bound", code="environment_provider_failure")
            result.extend(chunk)
        return bytes(result)

    async def write_stream(self, path: str, source: BinaryIO) -> None:
        size = remaining_bytes(source)
        if size > self.config.max_file_bytes:
            raise EnvironmentError("File exceeds the configured limit", code="environment_too_large")
        script = (
            "import signal,sys; signal.alarm(int(sys.argv[2])); "
            "f=open(sys.argv[1],'r+b'); remaining=int(sys.argv[3]); f.truncate(0)\n"
            "while remaining:\n"
            " chunk=sys.stdin.buffer.read(min(65536,remaining))\n"
            " if not chunk: sys.exit(1)\n"
            " f.write(chunk); remaining-=len(chunk)\n"
            "f.close()\n"
        )
        async with self._transfer(script, path, size=size) as connection:

            def send() -> None:
                raw = getattr(connection, "_sock", connection)
                total = 0
                while chunk := source.read(CHUNK_BYTES):
                    total += len(chunk)
                    if total > size:
                        raise EnvironmentError("Upload source changed", code="environment_provider_failure")
                    raw.sendall(chunk)
                raw.shutdown(socket.SHUT_WR)
                if total != size:
                    raise EnvironmentError("Upload source changed", code="environment_provider_failure")
                # Successful writes emit no stdout or stderr. Drain the Engine's
                # terminal frames before checking the exit status.
                for _stream, chunk in frames(connection):
                    if chunk:
                        raise EnvironmentError("Docker file write failed", code="environment_provider_failure")

            pending = asyncio.create_task(asyncio.to_thread(send))
            try:
                await asyncio.shield(pending)
            finally:
                # Join the worker before GuestFiles closes its local spool or
                # removes the candidate. A cancelled worker must stop writing.
                await asyncio.to_thread(disconnect, connection)
                await asyncio.gather(pending, return_exceptions=True)
