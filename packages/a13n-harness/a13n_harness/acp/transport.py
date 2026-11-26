"""ACP message transport and ownership of its process pipes."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from a13n_harness.environment.commands import (
    BoundProcessHandle,
)
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.environment.providers import BoundProcessOperations
from a13n_harness.environment.retention import EnvironmentOutputPolicy


class AcpError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AcpChannel(Protocol):
    """The public structural message interface accepted by the ACP SDK."""

    async def send(self, message: dict[str, Any]) -> None: ...
    async def receive(self) -> dict[str, Any] | None: ...
    async def close(self) -> None: ...


@runtime_checkable
class AcpTransport(Protocol):
    def open(self) -> AbstractAsyncContextManager[AcpChannel]: ...


_FRAME_BYTES = 16 * 1024 * 1024
_PROCESS_OUTPUT = EnvironmentOutputPolicy(max_inline_bytes=65536, max_output_bytes=64 * 1024 * 1024, overflow="retain")
_OUTPUT_PAGE = EnvironmentOutputPolicy(max_inline_bytes=65536, max_output_bytes=65536, overflow="truncate")
_RUNNING = frozenset({"starting", "running"})


def _decode(line: bytes) -> dict[str, Any]:
    if len(line) > _FRAME_BYTES:
        raise AcpError("acp_frame_too_large", "ACP frame exceeds the transport limit.")
    try:
        value = json.loads(line)
    except (ValueError, UnicodeError) as exc:
        raise AcpError("acp_invalid_frame", "ACP stdout is not a valid JSON frame.") from exc
    if not isinstance(value, dict):
        raise AcpError("acp_invalid_frame", "ACP frames must be JSON objects.")
    return value


def _encode(message: dict[str, Any]) -> bytes:
    data = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    if len(data) > _FRAME_BYTES:
        raise AcpError("acp_frame_too_large", "ACP frame exceeds the transport limit.")
    return data


@dataclass
class StreamChannel:
    """ACP over an already connected pair; the writer is closed on settlement."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter

    async def send(self, message: dict[str, Any]) -> None:
        self.writer.write(_encode(message))
        await self.writer.drain()

    async def receive(self) -> dict[str, Any] | None:
        pending = bytearray()
        while True:
            try:
                pending.extend(await self.reader.readuntil(b"\n"))
                if pending.strip():
                    return _decode(bytes(pending))
                pending.clear()
            except asyncio.LimitOverrunError as exc:
                pending.extend(await self.reader.readexactly(exc.consumed))
                if len(pending) > _FRAME_BYTES:
                    raise AcpError("acp_frame_too_large", "ACP frame exceeds the transport limit.") from exc
            except asyncio.IncompleteReadError as exc:
                if pending or exc.partial:
                    raise AcpError("acp_output_gap", "ACP stdout ended inside a frame.") from exc
                return None

    async def close(self) -> None:
        self.writer.close()
        with suppress(ConnectionError, TimeoutError):
            async with asyncio.timeout(0.25):
                await self.writer.wait_closed()


async def _stop_process(processes: BoundProcessOperations, handle: BoundProcessHandle) -> None:
    async with asyncio.timeout(8):
        with suppress(EnvironmentError):
            await processes.close_stdin(handle)
            await processes.wait(handle, condition="tree_cleaned", timeout_seconds=0.25)
        status = (await processes.inspect(handle)).status
        if status.phase in _RUNNING or status.cleanup != "complete":
            await processes.kill(handle)
            status = (await processes.wait(handle, condition="tree_cleaned", timeout_seconds=5)).status
            if status.cleanup != "complete":
                raise AcpError("acp_terminal_cleanup_incomplete", "The provider did not confirm terminal cleanup.")
        await processes.release(handle)


@dataclass(frozen=True)
class LocalStdioTransport:
    """Trusted same-machine invocation using the existing native process-tree owner."""

    executable: str
    cwd: str
    arguments: tuple[str, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict, repr=False)
    inherit_environment: bool = False

    @asynccontextmanager
    async def open(self) -> AsyncIterator[AcpChannel]:
        from a13n_harness.providers.environment.direct_local.processes import NativeProcessTrees

        environment = dict(os.environ) if self.inherit_environment else {}
        environment.update(self.environment)
        owner = NativeProcessTrees(terminate_grace_seconds=0.25)
        async with owner.open_pipes((self.executable, *self.arguments), Path(self.cwd), environment) as process:
            assert process.stdout is not None and process.stdin is not None
            async with _stream_channel(process.stdout, process.stdin) as channel:
                yield channel


@asynccontextmanager
async def _stream_channel(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> AsyncIterator[StreamChannel]:
    channel = StreamChannel(reader, writer)
    try:
        yield channel
    finally:
        await channel.close()
