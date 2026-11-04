"""Authorized EIP binary relays over per-transfer Redis Streams."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from a13n_envd_client.eip.v1 import DataFrame, DataFrameKind, DataResetStatus, decode_data_frame, encode_data_frame
from a13n_envd_client.errors import EIPClientError, EIPTransferTransportError
from a13n_envd_client.transport import TransferDirection
from a13n_harness.environment.models import EnvironmentError
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from redis.asyncio import Redis
from redis.exceptions import RedisError

from a13n_service.runs.environments.clients.streams import StreamPair
from a13n_service.runs.runtime import Runtime

if TYPE_CHECKING:
    from a13n_service.runs.environments.clients.sessions import Scope, Transfer

_READ = {DataFrameKind.ATTACH, DataFrameKind.CREDIT, DataFrameKind.RESET}
_WRITE = {DataFrameKind.ATTACH, DataFrameKind.CHUNK, DataFrameKind.END, DataFrameKind.RESET}
_ERRORS = (ValueError, EnvironmentError, EIPClientError, TimeoutError, OSError, RedisError, EOFError)


class Attach(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    handle: str = Field(min_length=1, max_length=128)
    direction: TransferDirection


class Attached(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    transfer_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    seconds: float = Field(gt=0, le=1800)


async def relay(runtime: Runtime, scope: Scope, transfer: Transfer, carrier: StreamPair) -> None:
    try:
        async with asyncio.timeout_at(carrier.deadline):
            await _pump(runtime, scope, transfer, carrier)
    except _ERRORS:
        pass  # Closing the streams fails the peer; never replay the native operation.
    finally:
        try:
            if scope.transfers.get(transfer.channel.handle) is transfer and not transfer.channel.peer_reset_received:
                if not transfer.finished:
                    scope.session.requester.retire_transfer(
                        transfer.channel,
                        reset=DataFrame(
                            kind=DataFrameKind.RESET,
                            session_id=scope.session.session_id,
                            handle=transfer.channel.handle,
                            reset_status=DataResetStatus.CANCELLED,
                        ),
                    )
        finally:
            await carrier.close()


async def stop(transfer: Transfer) -> None:
    if transfer.task is not None:
        transfer.task.cancel()
        await asyncio.gather(transfer.task, return_exceptions=True)


async def _pump(runtime: Runtime, scope: Scope, transfer: Transfer, carrier: StreamPair) -> None:
    channel = transfer.channel
    limits = scope.session.descriptor.limits

    async def upstream() -> None:
        first = True
        while True:
            raw = await carrier.receive()
            if not isinstance(raw, bytes):
                raise ValueError("Expected a binary transfer frame")
            frame = decode_data_frame(raw, max_frame_bytes=limits.max_transfer_frame_bytes)
            if (
                (frame.session_id, frame.handle) != (channel.session_id, channel.handle)
                or (first != (frame.kind is DataFrameKind.ATTACH))
                or frame.kind not in (_READ if transfer.direction == "read" else _WRITE)
                or frame.offset + len(frame.payload) > limits.max_file_transfer_bytes
            ):
                raise ValueError("Frame outside admitted transfer")
            first = False
            await scope.session.requester.send_data_frame(channel, frame)

    async def downstream() -> None:
        terminal = DataFrameKind.END if transfer.direction == "read" else DataFrameKind.END_ACK
        while True:
            frame = await channel.receive()
            if frame.offset + len(frame.payload) > limits.max_file_transfer_bytes:
                raise ValueError("Transfer exceeds its byte limit")
            await carrier.send(encode_data_frame(frame, max_frame_bytes=limits.max_transfer_frame_bytes))
            if frame.kind in {terminal, DataFrameKind.RESET}:
                transfer.finished = True
                # EIP readers can still send credit for buffered chunks after END.
                # Keep the pair until close/commit/abort, cancellation or the deadline.
                await asyncio.Future()

    async def verify() -> None:
        while True:
            await asyncio.sleep(runtime.settings.client_environments.heartbeat_seconds)
            await runtime.client_sessions.authorize(
                scope.request.model_copy(update={"caller": transfer.caller}), transfer.actions
            )

    tasks = [asyncio.create_task(work()) for work in (upstream, downstream, verify)]
    try:
        completed, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in completed:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@dataclass(slots=True)
class _Body:
    direction: TransferDirection
    stream: StreamPair | None = None
    reader: asyncio.Task[None] | None = None
    finished: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class WorkerStreams:
    def __init__(
        self,
        redis: Redis,
        environment_id: str,
        epoch: str,
        attach: Callable[[dict[str, object]], Awaitable[dict[str, JsonValue]]],
        limit: int,
        deliver: Callable[[DataFrame | EIPTransferTransportError], Awaitable[None]],
    ) -> None:
        self.redis, self.environment_id, self.epoch = redis, environment_id, epoch
        self.attach, self.limit, self.deliver = attach, limit, deliver
        self.bodies: dict[tuple[str, str], _Body] = {}
        self.closing: set[asyncio.Task[None]] = set()

    def register(self, session: str, handle: str, direction: TransferDirection) -> None:
        if (session, handle) in self.bodies:
            raise ValueError("Transfer already registered")
        self.bodies[session, handle] = _Body(direction)

    def unregister(self, session: str, handle: str) -> None:
        body = self.bodies.pop((session, handle), None)
        if body is not None:
            if body.reader is not None:
                body.reader.cancel()
            if body.stream is not None:
                task = asyncio.create_task(body.stream.close())
                self.closing.add(task)
                task.add_done_callback(self.closing.discard)

    async def send(self, frame: DataFrame) -> None:
        key = (frame.session_id, frame.handle)
        body = self.bodies.get(key)
        if body is None:
            raise ValueError("Unknown client transfer")
        try:
            async with body.lock:
                if frame.kind is DataFrameKind.RESET:
                    # Closing this carrier retires the native body on Control.
                    # Cleanup must also work after the stream's deadline/expiry;
                    # trying to publish RESET there would fail the whole Session.
                    if body.stream is not None:
                        await body.stream.close()
                    return
                if body.stream is None:
                    if frame.kind is not DataFrameKind.ATTACH:
                        raise ValueError("Transfer must attach first")
                    attached = Attached.model_validate(
                        await self.attach(
                            {
                                "session_id": key[0],
                                "handle": key[1],
                                "direction": body.direction,
                            }
                        )
                    )
                    stream = StreamPair(
                        self.redis,
                        self.environment_id,
                        self.epoch,
                        attached.transfer_id,
                        side="worker",
                        limit=self.limit,
                        seconds=attached.seconds,
                    )
                    if self.bodies.get(key) is not body:
                        await stream.close()
                        raise ValueError("Transfer attachment changed")
                    body.stream = stream
                    body.reader = asyncio.create_task(self._read(body, key))
                await body.stream.send(encode_data_frame(frame, max_frame_bytes=self.limit))
        except _ERRORS as error:
            raise EIPTransferTransportError("Client transfer send failed", session_id=key[0], handle=key[1]) from error

    async def _read(self, body: _Body, key: tuple[str, str]) -> None:
        assert body.stream is not None
        terminal = DataFrameKind.END if body.direction == "read" else DataFrameKind.END_ACK
        try:
            while True:
                frame = decode_data_frame(await body.stream.receive(), max_frame_bytes=self.limit)
                if (frame.session_id, frame.handle) != key:
                    raise ValueError("Mismatched transfer frame")
                await self.deliver(frame)
                if frame.kind in {terminal, DataFrameKind.RESET}:
                    body.finished = True
                    return
        except _ERRORS:
            await self.deliver(
                EIPTransferTransportError("Client transfer interrupted", session_id=key[0], handle=key[1])
            )
        finally:
            if not body.finished:
                await body.stream.close()

    async def close(self) -> None:
        readers = [body.reader for body in self.bodies.values() if body.reader is not None]
        for session, handle in tuple(self.bodies):
            self.unregister(session, handle)
        await asyncio.gather(*readers, *self.closing, return_exceptions=True)
