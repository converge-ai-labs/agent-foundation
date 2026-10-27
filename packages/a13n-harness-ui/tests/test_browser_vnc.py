from __future__ import annotations

import asyncio
import struct

import pytest
from a13n_harness_ui import browser_vnc
from a13n_harness_ui.browser_vnc import VERSION, challenge_response, screen_sharing

pytestmark = pytest.mark.anyio

PASSWORD = bytes.fromhex("c82cea9ed93dfb8f").decode("latin-1")
CHALLENGE = bytes.fromhex("0123456789abcdef") * 2
RESPONSE = bytes.fromhex("85e813540f0ab405") * 2
INIT = (
    struct.pack(">HH", 1440, 900)
    + struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
    + struct.pack(">I", 3)
    + b"Mac"
)
KEY = struct.pack(">BBHI", 4, 1, 0, 0xFFE7)
POINTER = struct.pack(">BBHH", 5, 1, 42, 80)
UPDATE = struct.pack(">BBHHHH", 3, 1, 0, 0, 1440, 900)


async def test_vnc_challenge_uses_bit_reversed_password_not_password_file_key():
    assert challenge_response(PASSWORD, CHALLENGE) == RESPONSE


@pytest.fixture
async def native(monkeypatch):
    accepted = asyncio.Queue()
    finished = []

    async def serve(reader, writer):
        done = asyncio.get_running_loop().create_future()
        finished.append(done)
        try:
            writer.write(b"RFB 003.889\n")
            assert await reader.readexactly(12) == VERSION
            writer.write(b"\x02\x1e\x02")  # ARD is first; Host must select classic VNC.
            assert await reader.readexactly(1) == b"\x02"
            writer.write(CHALLENGE)
            assert await reader.readexactly(16) == RESPONSE
            writer.write(b"\0\0\0\0")
            assert await reader.readexactly(1) == b"\x01"
            writer.write(INIT)
            await writer.drain()
            await accepted.put((reader, writer, done))
            await done
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    open_connection = asyncio.open_connection

    async def connect(host, port):
        assert (host, port) == ("127.0.0.1", 5900)
        return await open_connection("127.0.0.1", server.sockets[0].getsockname()[1])

    monkeypatch.setattr(asyncio, "open_connection", connect)
    async with server:
        try:
            yield accepted
        finally:
            for done in finished:
                if not done.done():
                    done.set_result(None)


async def viewer(connection):
    assert await connection.read(65536) == VERSION
    # Exercise arbitrary fragments, including the shared flag being ignored.
    await connection.send(VERSION[:3])
    await connection.send(VERSION[3:] + b"\x01\x00")
    assert await connection.read(65536) == b"\x01\x01"
    assert await connection.read(65536) == b"\0\0\0\0"
    assert await connection.read(65536) == INIT


@pytest.mark.parametrize("controlling", [False, True])
async def test_native_auth_framebuffer_and_input_authority(native, controlling):
    connection = await screen_sharing(PASSWORD, controlling=controlling)
    reader, writer, done = await native.get()
    await viewer(connection)
    writer.write(b"native framebuffer bytes")
    assert await connection.read(65536) == b"native framebuffer bytes"
    clipboard = b"\x06\0\0\0" + struct.pack(">i", -4) + b"text"
    payload = KEY + POINTER + clipboard + UPDATE
    for index in range(0, len(payload), 3):
        await connection.send(payload[index : index + 3])
    expected = payload if controlling else UPDATE
    assert await asyncio.wait_for(reader.readexactly(len(expected)), 1) == expected
    connection.close()
    await connection.wait_closed()
    released = await asyncio.wait_for(reader.read(), 1)
    assert released == (struct.pack(">BBHI", 4, 0, 0, 0xFFE7) + b"\x05\0" + POINTER[2:] if controlling else b"")
    await connection.send(KEY)
    assert await reader.read() == b""
    done.set_result(None)


async def test_negotiates_only_supported_encodings(native):
    connection = await screen_sharing(PASSWORD, controlling=True)
    reader, _, done = await native.get()
    await viewer(connection)
    encodings = [0, -239, -258, -308, -309, -316, -312, 0xC0A1E5CE]
    await connection.send(
        struct.pack(">BBH", 2, 0, len(encodings)) + b"".join(struct.pack(">I", n & 0xFFFFFFFF) for n in encodings)
    )
    expected = struct.pack(">BBH4I", 2, 0, 4, 0, -239 & 0xFFFFFFFF, -312 & 0xFFFFFFFF, 0xC0A1E5CE)
    assert await asyncio.wait_for(reader.readexactly(len(expected)), 1) == expected
    connection.close()
    await connection.wait_closed()
    done.set_result(None)


@pytest.mark.parametrize("message", [b"\xfa", b"\xfb", b"\xff", b"\x05\x80", b"\x06\0\0\0\x7f\xff\xff\xff"])
async def test_rejects_power_resize_unnegotiated_input_and_oversized_clipboard(native, message):
    connection = await screen_sharing(PASSWORD, controlling=True)
    reader, _, done = await native.get()
    await viewer(connection)
    with pytest.raises(OSError):
        await connection.send(message)
    connection.close()
    await connection.wait_closed()
    assert await asyncio.wait_for(reader.read(), 1) == b""
    done.set_result(None)


@pytest.mark.parametrize("password", ["", "ninechars", "你好", "a\0b"])
async def test_invalid_password_never_connects(monkeypatch, password):
    async def unexpected(*args):
        pytest.fail("invalid password opened a socket")

    monkeypatch.setattr(asyncio, "open_connection", unexpected)
    with pytest.raises(ValueError):
        await browser_vnc.screen_sharing(password, controlling=False)


@pytest.mark.parametrize("controlling", [False, True])
async def test_http_ticket_websocket_gateway_and_handback(native, tmp_path, monkeypatch, controlling):
    from a13n_harness_ui.browser import Binding
    from a13n_harness_ui.browser_macos import PASSWORD_ENV, MacDesktop
    from starlette.websockets import WebSocket

    from .test_sharing_webui import shared_server

    monkeypatch.setenv(PASSWORD_ENV, PASSWORD)
    async with shared_server(tmp_path, skip_auth=True, share_computer=True) as (server, app, client):
        thread = (await app.create_thread()).thread_id
        host = app.browsers()
        host.desktop = MacDesktop()
        host.bindings[thread] = Binding(status="ready")
        identity = host.state(thread).session_id
        token = (await host.control(thread, identity, human=True)).control_token if controlling else ""
        response = await client.post(
            f"/api/threads/{thread}/browser/access", json={"session_id": identity, "control_token": token}
        )
        assert response.status_code == 200, response.text
        access = response.json()
        assert access["password"] == "" and access["control"] is controlling
        inbound, outbound = asyncio.Queue(), asyncio.Queue()
        inbound.put_nowait({"type": "websocket.connect"})
        socket = WebSocket({"type": "websocket", "subprotocols": ["binary"]}, inbound.get, outbound.put)
        endpoint = next(
            route.endpoint for route in server.routes if route.path == "/api/threads/{thread_id}/browser/connect"
        )
        task = asyncio.create_task(endpoint(socket, thread, access["ticket"]))
        try:
            async with asyncio.timeout(5):
                reader, writer, done = await native.get()
                assert (await outbound.get())["type"] == "websocket.accept"
                assert (await outbound.get())["bytes"] == VERSION
                inbound.put_nowait({"type": "websocket.receive", "bytes": VERSION + b"\x01\x01"})
                for expected in (b"\x01\x01", b"\0\0\0\0", INIT):
                    assert (await outbound.get())["bytes"] == expected
                inbound.put_nowait({"type": "websocket.receive", "bytes": KEY[:2]})
                inbound.put_nowait({"type": "websocket.receive", "bytes": KEY[2:] + UPDATE})
                expected = KEY + UPDATE if controlling else UPDATE
                assert await reader.readexactly(len(expected)) == expected
                writer.write(b"framebuffer")
                assert (await outbound.get())["bytes"] == b"framebuffer"
                await host.control(thread, identity, human=False)
                assert await reader.read() == (struct.pack(">BBHI", 4, 0, 0, 0xFFE7) if controlling else b"")
                await task
                assert not host.connections and host.released.is_set()
                # A consumed ticket cannot reconnect, even after handback.
                again = WebSocket({"type": "websocket"}, inbound.get, outbound.put)
                await endpoint(again, thread, access["ticket"])
                messages = []
                while not outbound.empty():
                    messages.append(outbound.get_nowait())
                assert messages[-1] == {"type": "websocket.close", "code": 4403, "reason": ""}
                done.set_result(None)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
