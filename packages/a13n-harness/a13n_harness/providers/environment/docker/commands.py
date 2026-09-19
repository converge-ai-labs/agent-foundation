"""Bounded one-shot Docker exec helpers; no guest daemon or network transport."""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import BinaryIO

from pydantic import JsonValue, TypeAdapter

from .._guest_commands import decoded_bytes, file_helper_source
from ..models import EnvironmentError, EnvironmentOperationReceipt
from .configuration import DockerEnvironmentConfiguration
from .errors import engine_errors
from .observation import attach, disconnect, frames
from .runtime import DockerSDKEngine

_OBJECT = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True)
class DockerFileConfiguration:
    max_file_bytes: int
    read_only: bool = False


class DockerCommands:
    def __init__(self, engine: DockerSDKEngine, container_id: str, config: DockerEnvironmentConfiguration) -> None:
        self.closed = False
        self.engine = engine
        self.container_id = container_id
        self.config = config
        self.configuration = DockerFileConfiguration(config.max_file_bytes)
        self.mount_id = "workspace"
        self._operations = itertools.count(1)
        self._source = file_helper_source()

    def receipt(self) -> EnvironmentOperationReceipt:
        return EnvironmentOperationReceipt(
            mount_id=self.mount_id,
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
                "read_only": False,
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

    async def read_stream(self, path: str) -> AsyncIterator[bytes]:
        offset = 0
        logical = path
        while True:
            chunk = decoded_bytes(await self.files("read", {"path": logical, "offset": offset, "length": 65536}))
            if not chunk:
                break
            yield chunk
            offset += len(chunk)

    async def write_stream(self, path: str, source: BinaryIO) -> None:
        # Native exec writes a bounded chunk as the configured user. The shared
        # file layer owns staging, publication, limits, and failure cleanup.
        while chunk := await asyncio.to_thread(source.read, 49152):
            await self.execute(
                [
                    self.config.python,
                    "-I",
                    "-c",
                    "import base64,sys; f=open(sys.argv[1],'ab'); f.write(base64.b64decode(sys.argv[2])); f.close()",
                    path,
                    base64.b64encode(chunk).decode(),
                ],
                maximum=65536,
            )

    async def port(self, port: int) -> dict[str, JsonValue]:
        output = await self.execute(
            [
                self.config.python,
                "-I",
                "-c",
                "import socket,sys; s=socket.socket(); s.settimeout(.1); "
                "print(int(s.connect_ex(('127.0.0.1',int(sys.argv[1])))==0)); s.close()",
                str(port),
            ]
        )
        return {"listening": output.strip() == b"1"}
