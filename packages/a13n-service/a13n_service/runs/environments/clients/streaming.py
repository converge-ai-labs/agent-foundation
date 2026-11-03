"""Private, bounded EIP binary streams between a Worker and the connection's Control."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import anyio
from a13n_envd_client.eip.v1 import DataFrame, DataFrameKind, DataResetStatus, decode_data_frame, encode_data_frame
from a13n_envd_client.errors import EIPClientError, EIPTransferTransportError
from a13n_envd_client.http import normalize_http_endpoint
from a13n_envd_client.transport import TransferDirection
from a13n_harness.environment.models import EnvironmentError
from fastapi import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException
from websockets.typing import Subprotocol

from a13n_service.infra.errors import ServiceError
from a13n_service.infra.websocket import AcceptedSocket, connection_token
from a13n_service.runs.runtime import Runtime

if TYPE_CHECKING:
    from a13n_service.runs.environments.clients.sessions import Scope, Transfer

PATH = "/api/v1/client-environments/transfer"
_READ = {DataFrameKind.ATTACH, DataFrameKind.CREDIT, DataFrameKind.RESET}
_WRITE = {DataFrameKind.ATTACH, DataFrameKind.CHUNK, DataFrameKind.END, DataFrameKind.RESET}


def endpoint(runtime: Runtime) -> str:
    origin = normalize_http_endpoint(
        runtime.settings.client_environments.transfer_origin
        or runtime.settings.server.control_url
        or runtime.settings.server.public_url
    )
    parsed = urlsplit(origin)
    return urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, PATH, "", ""))


class _Direct(connect):
    def process_redirect(self, exc: Exception) -> Exception:
        return exc  # A redirect must not receive the transfer credential.


async def relay(socket: WebSocket) -> None:
    carrier = AcceptedSocket(socket)
    runtime: Runtime = socket.app.state.runtime
    try:
        # The internal origin only permits plaintext on loopback.
        token = connection_token(socket, allow_loopback_ws=True)
        scope = runtime.client_sessions.scopes.get(socket.headers.get("x-a13n-scope", ""))
        if scope is None or not secrets.compare_digest(scope.transfer_token, token):
            raise ValueError("Invalid stream scope")
        handle = socket.headers.get("eip-transfer-handle", "")
        transfer = scope.transfers.get(handle)
        if (
            transfer is None
            or transfer.direction != socket.headers.get("eip-transfer-direction")
            or scope.session.session_id != socket.headers.get("eip-session")
        ):
            raise ValueError("Invalid transfer selector")
        request = scope.request.model_copy(update={"caller": transfer.caller})
        await runtime.client_sessions.authorize(request, transfer.actions)
        if transfer.attached or scope.transfers.get(handle) is not transfer:
            raise ValueError("Transfer is already attached or ended")
        transfer.attached = True
        try:
            await socket.accept(subprotocol="eip.v1")
            async with asyncio.timeout(runtime.settings.client_environments.transfer_seconds):
                await _pump(runtime, scope, transfer, carrier)
        finally:
            # An attachment is one-shot. On interruption, retire its native body;
            # commit/close still require a separate authorized control request.
            if scope.transfers.get(handle) is transfer and not transfer.channel.peer_reset_received:
                if not transfer.finished:
                    scope.session.requester.retire_transfer(
                        transfer.channel,
                        reset=DataFrame(
                            kind=DataFrameKind.RESET,
                            session_id=scope.session.session_id,
                            handle=handle,
                            reset_status=DataResetStatus.CANCELLED,
                        ),
                    )
    except (
        ValueError,
        ServiceError,
        EnvironmentError,
        EIPClientError,
        TimeoutError,
        OSError,
        EOFError,
        WebSocketDisconnect,
    ):
        with anyio.move_on_after(1, shield=True):
            await carrier.close(1008)
    finally:
        with anyio.move_on_after(1, shield=True):
            await carrier.close()


async def _pump(runtime: Runtime, scope: Scope, transfer: Transfer, carrier: AcceptedSocket) -> None:
    channel = transfer.channel
    limits = scope.session.descriptor.limits

    async def upstream() -> None:
        first = True
        while True:
            raw = await carrier.recv()
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
                return

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
    socket: ClientConnection | None = None
    reader: asyncio.Task[None] | None = None


class WorkerStreams:
    def __init__(
        self,
        url: str,
        scope_id: str,
        token: str,
        limit: int,
        deliver: Callable[[DataFrame | EIPTransferTransportError], Awaitable[None]],
    ) -> None:
        self.url, self.scope_id, self.token, self.limit, self.deliver = url, scope_id, token, limit, deliver
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
            if body.socket is not None:
                task = asyncio.create_task(body.socket.close())
                self.closing.add(task)
                task.add_done_callback(self.closing.discard)

    async def send(self, frame: DataFrame) -> None:
        key = (frame.session_id, frame.handle)
        body = self.bodies.get(key)
        if body is None:
            raise ValueError("Unknown client transfer")
        try:
            if body.socket is None:
                if frame.kind is not DataFrameKind.ATTACH:
                    raise ValueError("Transfer must attach first")
                socket = await _Direct(
                    self.url,
                    subprotocols=[Subprotocol("eip.v1")],
                    compression=None,
                    proxy=None,
                    additional_headers={
                        "Authorization": f"Bearer {self.token}",
                        "X-A13N-Scope": self.scope_id,
                        "EIP-Session": key[0],
                        "EIP-Transfer-Handle": key[1],
                        "EIP-Transfer-Direction": body.direction,
                    },
                    max_size=self.limit,
                    max_queue=8,
                    open_timeout=5,
                    close_timeout=1,
                )
                if self.bodies.get(key) is not body or socket.subprotocol != "eip.v1":
                    await socket.close()
                    raise ValueError("Transfer attachment changed")
                body.socket = socket
                body.reader = asyncio.create_task(self._read(body, key))
            await body.socket.send(encode_data_frame(frame, max_frame_bytes=self.limit))
        except (ValueError, OSError, WebSocketException, TimeoutError) as error:
            raise EIPTransferTransportError("Client transfer send failed", session_id=key[0], handle=key[1]) from error

    async def _read(self, body: _Body, key: tuple[str, str]) -> None:
        assert body.socket is not None
        terminal = DataFrameKind.END if body.direction == "read" else DataFrameKind.END_ACK
        try:
            while True:
                raw = await body.socket.recv()
                if not isinstance(raw, bytes):
                    raise ValueError("Unexpected non-binary frame")
                frame = decode_data_frame(raw, max_frame_bytes=self.limit)
                if (frame.session_id, frame.handle) != key:
                    raise ValueError("Mismatched transfer frame")
                await self.deliver(frame)
                if frame.kind in {terminal, DataFrameKind.RESET}:
                    return
        except (ValueError, OSError, WebSocketException, TimeoutError):
            await self.deliver(
                EIPTransferTransportError("Client transfer interrupted", session_id=key[0], handle=key[1])
            )

    async def close(self) -> None:
        readers = [body.reader for body in self.bodies.values() if body.reader is not None]
        for session, handle in tuple(self.bodies):
            self.unregister(session, handle)
        await asyncio.gather(*readers, *self.closing, return_exceptions=True)
        self.token = ""
