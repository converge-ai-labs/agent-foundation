from __future__ import annotations

import asyncio
from typing import cast

import pytest
from a13n_envd_client import (
    AcceptedWebSocketTransport,
    ControlFrame,
    EIPProtocolError,
)
from a13n_envd_client.eip.v1 import DataFrame, DataFrameKind, encode_data_frame
from websockets.asyncio.server import ServerConnection


class FakeAcceptedConnection:
    def __init__(self, *, subprotocol: str | None = "eip.v1") -> None:
        self.subprotocol = subprotocol
        self.incoming: asyncio.Queue[str | bytes] = asyncio.Queue()
        self.sent: list[str | bytes] = []
        self.closed_with: int | None = None

    async def recv(self) -> str | bytes:
        return await self.incoming.get()

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


def accepted(connection: FakeAcceptedConnection) -> AcceptedWebSocketTransport:
    return AcceptedWebSocketTransport(cast(ServerConnection, connection))


def test_accepted_websocket_requires_exact_eip_subprotocol() -> None:
    connection = FakeAcceptedConnection(subprotocol=None)
    with pytest.raises(ValueError, match=r"eip\.v1"):
        accepted(connection)


def test_websocket_transport_maps_text_and_binary_messages() -> None:
    async def scenario() -> None:
        connection = FakeAcceptedConnection()
        transport = accepted(connection)
        data = DataFrame(kind=DataFrameKind.CHUNK, handle="reader-one", payload=b"payload")
        await connection.incoming.put('{"jsonrpc":"2.0"}')
        await connection.incoming.put(encode_data_frame(data, max_frame_bytes=1024 * 1024))

        assert await transport.receive() == ControlFrame(b'{"jsonrpc":"2.0"}')
        assert await transport.receive() == data
        await transport.close()

    asyncio.run(scenario())


def test_websocket_transport_sends_control_as_text_and_data_as_binary() -> None:
    async def scenario() -> None:
        connection = FakeAcceptedConnection()
        transport = accepted(connection)
        data = DataFrame(kind=DataFrameKind.CHUNK, handle="writer-one", payload=b"payload")

        await transport.send(ControlFrame(b'{"jsonrpc":"2.0"}'))
        await transport.send(data)

        assert connection.sent[0] == '{"jsonrpc":"2.0"}'
        assert isinstance(connection.sent[1], bytes)
        await transport.close()

    asyncio.run(scenario())


def test_websocket_transport_closes_on_invalid_binary_message() -> None:
    async def scenario() -> None:
        connection = FakeAcceptedConnection()
        transport = accepted(connection)
        await connection.incoming.put(b"not-an-eip-data-frame")

        with pytest.raises(EIPProtocolError, match="invalid EIP WebSocket data frame"):
            await transport.receive()
        assert connection.closed_with == 1002

    asyncio.run(scenario())
