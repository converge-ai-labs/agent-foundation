"""Run-local bounded byte observations; disconnecting never signals a process."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from .._local_retention import LocalRetentionStore
from ..commands import ProcessStreamRead
from ..models import EnvironmentError
from ..retention import EnvironmentOutputCapture, EnvironmentOutputPolicy, EnvironmentOutputSegment


def frames(connection: Any) -> Iterator[tuple[int, bytes]]:
    from docker.utils.socket import next_frame_header, read

    while True:
        stream, remaining = next_frame_header(connection)
        if remaining < 0:
            return
        if stream not in (1, 2):
            raise EnvironmentError("Invalid Docker output stream", code="environment_provider_failure")
        while remaining:
            chunk = read(connection, min(remaining, 65536))
            if not chunk:
                return
            remaining -= len(chunk)
            yield stream, chunk


def disconnect(connection: Any) -> None:
    raw = getattr(connection, "_sock", connection)
    try:
        raw.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


async def attach(api: Any, token: str) -> Any:
    # The SDK call runs in a thread. Cancellation must still close a socket
    # returned after the awaiting task has been cancelled.
    pending = asyncio.create_task(asyncio.to_thread(api.exec_start, token, socket=True, tty=False))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        try:
            connection = await pending
            await asyncio.to_thread(disconnect, connection)
            response = getattr(connection, "_response", None)
            await asyncio.to_thread(response.close if response is not None else connection.close)
        except Exception:
            pass
        raise


@dataclass(slots=True)
class ByteBudget:
    limit: int
    used: int = 0


@dataclass(slots=True)
class StreamObservation:
    limit: int
    budget: ByteBudget
    data: bytearray = field(default_factory=bytearray)
    produced: int = 0
    closed: bool = False
    complete: bool = False

    def append(self, data: bytes) -> None:
        accepted = min(len(data), self.limit - len(self.data), self.budget.limit - self.budget.used)
        self.data.extend(data[:accepted])
        self.budget.used += accepted
        self.produced += len(data)

    def release(self) -> None:
        self.budget.used -= len(self.data)
        self.data.clear()

    def read(self, offset: int, policy: EnvironmentOutputPolicy) -> ProcessStreamRead:
        if not 0 <= offset <= len(self.data):
            raise EnvironmentError("Invalid Docker output offset", code="environment_cursor_invalid")
        chunk = bytes(self.data[offset : offset + min(policy.max_inline_bytes, policy.max_output_bytes)])
        dropped = self.produced - len(self.data)
        capture = EnvironmentOutputCapture(
            kind="inline" if chunk else "empty",
            inline=chunk,
            producer_complete=self.complete,
            observation_closed=self.closed,
            content_complete=self.complete and not dropped,
            coverage="complete" if self.complete and not dropped else "partial",
            reason="observation_limit" if dropped else "connection_lost" if self.closed and not self.complete else None,
            produced_bytes=self.produced if self.complete else None,
            captured_bytes=len(self.data),
            dropped_bytes=dropped,
            available_end=len(self.data),
        )
        return ProcessStreamRead(
            chunks=(EnvironmentOutputSegment(start_offset=offset, data=chunk),) if chunk else (),
            next_cursor=None,
            capture=capture,
        )

    async def materialize(
        self, policy: EnvironmentOutputPolicy, retention: LocalRetentionStore
    ) -> EnvironmentOutputCapture:
        size = min(len(self.data), policy.max_output_bytes)
        dropped = self.produced - size
        if policy.overflow == "fail" and (dropped or size > policy.max_inline_bytes):
            raise EnvironmentError("Docker output exceeds requested limit", code="environment_too_large")
        if policy.overflow == "retain" and size > policy.max_inline_bytes:
            writer = await retention.reserve(max_bytes=policy.max_output_bytes)
            try:
                accepted = await writer.write(bytes(self.data[:size]))
                ref = await writer.commit(
                    producer_complete=self.complete,
                    content_complete=self.complete and accepted == self.produced,
                    produced_bytes=self.produced,
                    dropped_bytes=self.produced - accepted,
                )
            except BaseException:
                await writer.abort()
                raise
            return (await retention.read(ref, policy=policy)).capture
        size = min(size, policy.max_inline_bytes)
        data = bytes(self.data[:size])
        return EnvironmentOutputCapture(
            kind="truncated" if size < self.produced else "inline" if size else "empty",
            inline=data,
            producer_complete=self.complete,
            content_complete=self.complete and size == self.produced,
            observation_closed=self.closed,
            produced_bytes=self.produced if self.complete else None,
            captured_bytes=size,
            dropped_bytes=self.produced - size,
            available_end=size,
        )


@dataclass(slots=True)
class ProcessObservation:
    connection: Any
    stdout: StreamObservation
    stderr: StreamObservation
    stdin_open: bool
    detached: bool = False
    task: asyncio.Task[None] | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def consume(self) -> None:
        iterator = frames(self.connection)
        try:
            while frame := await asyncio.to_thread(next, iterator, None):
                stream, chunk = frame
                (self.stdout if stream == 1 else self.stderr).append(chunk)
                self.changed.set()
            self.stdout.complete = self.stderr.complete = not self.detached
        except (OSError, EnvironmentError):
            pass
        finally:
            self.stdout.closed = self.stderr.closed = True
            self.changed.set()

    async def close(self) -> None:
        if not self.stdout.closed:
            self.detached = True
            await asyncio.to_thread(disconnect, self.connection)
        if self.task is not None:
            await self.task
        response = getattr(self.connection, "_response", None)
        if response is not None:
            await asyncio.to_thread(response.close)
        else:
            await asyncio.to_thread(self.connection.close)
        self.stdin_open = False
