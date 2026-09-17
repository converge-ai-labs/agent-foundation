from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from time import monotonic

import pytest
from a13n_service.environments.websocket.authority import (
    ConnectionIdentity,
    DispatchAuthority,
    DispatchDenied,
    LeaseDeadline,
    UseIdentity,
)
from a13n_service.environments.websocket.transport import ClientWebSocket
from starlette.websockets import WebSocket

pytestmark = pytest.mark.anyio
CONNECTION = ConnectionIdentity("org", "env", "ec", "ece", "control")
USE = UseIdentity(CONNECTION, "use", "run", "attempt", 1, "worker")


def grant(identity=CONNECTION, seconds=10):
    return DispatchAuthority(identity, LeaseDeadline(monotonic() + seconds))


@asynccontextmanager
async def carrier(**limits):
    inbound = asyncio.Queue()
    outbound = []

    async def send(message):
        outbound.append(message)

    await inbound.put({"type": "websocket.connect"})
    websocket = WebSocket({"type": "websocket"}, inbound.get, send)
    await websocket.accept(subprotocol="eip.v1")
    client = ClientWebSocket(websocket, **limits)
    reader = asyncio.create_task(client.read_messages())
    try:
        yield client, inbound, outbound
    finally:
        await client.close()
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


async def test_candidate_receives_disconnect_without_sdk_reader():
    async with carrier() as (client, inbound, _):
        with pytest.raises(OSError, match="no dispatch authority"):
            await client.send("initialize")
        await inbound.put({"type": "websocket.disconnect", "code": 1000})
        async with asyncio.timeout(1):
            await client.wait_closed()
        with pytest.raises(EOFError):
            await client.recv()


async def test_wire_order_and_final_message_survive_disconnect():
    async with carrier() as (client, inbound, _):
        await inbound.put({"type": "websocket.receive", "text": "response"})
        await inbound.put({"type": "websocket.receive", "bytes": b"data"})
        await inbound.put({"type": "websocket.disconnect", "code": 1000})
        await client.wait_closed()
        assert await client.recv() == "response"
        assert await client.recv() == b"data"
        with pytest.raises(EOFError):
            await client.recv()


async def test_local_close_wakes_pending_recv_and_never_reads_socket():
    async with carrier() as (client, _, _):
        pending = asyncio.create_task(client.recv())
        await client.close()
        with pytest.raises(EOFError):
            await pending
        with pytest.raises(RuntimeError, match="receive owner"):
            await client.read_messages()


@pytest.mark.parametrize("messages,limit", [(["first", "second"], 20), (["\u4f60\u597d"], 5)])
async def test_mailbox_and_utf8_byte_limits_close_carrier(messages, limit):
    async with carrier(max_messages=1, max_message_bytes=limit) as (client, inbound, outbound):
        for message in messages:
            await inbound.put({"type": "websocket.receive", "text": message})
        async with asyncio.timeout(1):
            await client.wait_closed()
        assert outbound[-1] == {"type": "websocket.close", "code": 1009, "reason": ""}


async def test_initialization_authority_cannot_bypass_use_or_be_replaced():
    async with carrier() as (client, _, outbound):
        connection = grant()
        client.bind_connection(connection)
        await client.send("initialize")
        client.require_use()
        with pytest.raises(OSError, match="authority"):
            await client.send("operation")
        use = grant(USE)
        client.bind_use(use)
        await client.send(b"operation")
        await use.fence()
        with pytest.raises(OSError, match="authority"):
            await client.send("late operation")
        with pytest.raises(ValueError, match="exactly one use"):
            client.bind_use(grant(USE))
        with pytest.raises(ValueError, match="exactly one connection"):
            client.bind_connection(grant())
        assert [message for message in outbound if message["type"] == "websocket.send"] == [
            {"type": "websocket.send", "text": "initialize"},
            {"type": "websocket.send", "bytes": b"operation"},
        ]


async def test_connection_fence_denies_even_unexpired_use():
    async with carrier() as (client, _, outbound):
        connection = grant()
        client.bind_connection(connection)
        client.bind_use(grant(USE))
        await connection.fence()
        with pytest.raises(OSError, match="authority"):
            await client.send("late operation")
        assert not any(message["type"] == "websocket.send" for message in outbound)


async def test_mismatched_use_is_never_attached():
    async with carrier() as (client, _, _):
        client.bind_connection(grant())
        other = ConnectionIdentity("org", "env", "other", "epoch", "control")
        with pytest.raises(ValueError, match="exactly one use"):
            client.bind_use(grant(UseIdentity(other, "use", "run", "attempt", 1, "worker")))


async def test_blocked_socket_write_is_cancelled_before_retirement_ack():
    inbound = asyncio.Queue()
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def send(message):
        if message["type"] == "websocket.send":
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    await inbound.put({"type": "websocket.connect"})
    websocket = WebSocket({"type": "websocket"}, inbound.get, send)
    await websocket.accept(subprotocol="eip.v1")
    client = ClientWebSocket(websocket)
    connection = grant(seconds=0.2)
    client.bind_connection(connection)
    operation = asyncio.create_task(client.send("blocked"))
    async with asyncio.timeout(1):
        await entered.wait()
        await connection.fence()
    assert cancelled.is_set()
    with pytest.raises(OSError, match="authority"):
        await operation
    await client.close()


async def test_invalidated_candidate_cannot_later_bind_a_fresh_grant():
    async with carrier() as (client, _, outbound):
        client.invalidate()
        with pytest.raises(DispatchDenied):
            client.bind_connection(grant())
        with pytest.raises(OSError):
            await client.send("initialize")
        assert not any(event["type"] == "websocket.send" for event in outbound)
