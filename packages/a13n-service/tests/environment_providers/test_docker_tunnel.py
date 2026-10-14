"""Tunnel framing and scope cleanup without a Docker daemon."""

import asyncio
import socket
import threading
from unittest.mock import Mock

import pytest
from a13n_harness.providers.environment.docker import tunnel
from a13n_harness.providers.environment.docker.runtime import DockerSDKEngine

pytestmark = pytest.mark.anyio


async def test_tunnel_closes_accepted_socket_when_cancelled_before_forwarding_starts():
    engine = DockerSDKEngine(Mock())
    writer = Mock()
    async with tunnel.DesktopTunnel(engine, "owned-container") as forwarding:
        forwarding._accept(asyncio.StreamReader(), writer)
        # Exit without yielding: the accepted task has never entered its try/finally.
        assert len(forwarding._clients) == 1
    writer.close.assert_called_once()
    engine.client.api.exec_create.assert_not_called()
    assert not forwarding._clients


async def test_scope_exit_finishes_cleanup_already_started_by_http_eof(monkeypatch):
    engine = DockerSDKEngine(Mock())
    engine.client.api.exec_create.return_value = {"Id": "test-exec"}
    client, remote = socket.socketpair()
    attached, cleaning = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    disconnect = tunnel.disconnect

    async def attach(api, identity):
        attached.set()
        return client

    def blocked_disconnect(connection):
        disconnect(connection)
        loop.call_soon_threadsafe(cleaning.set)
        assert release.wait(5)

    monkeypatch.setattr(tunnel, "attach", attach)
    monkeypatch.setattr(tunnel, "disconnect", blocked_disconnect)
    forwarding = await tunnel.DesktopTunnel(engine, "owned-container").__aenter__()
    try:
        _, writer = await asyncio.open_connection("127.0.0.1", int(forwarding.endpoint.rsplit(":", 1)[1]))
        await asyncio.wait_for(attached.wait(), 2)
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(cleaning.wait(), 2)
        task = next(iter(forwarding._clients))
        closing = asyncio.create_task(forwarding.__aexit__())
        async with asyncio.timeout(2):
            while not task.cancelling():
                await asyncio.sleep(0)
            release.set()
            await closing
        assert client.fileno() == -1
        assert not forwarding._clients
    finally:
        release.set()
        await forwarding.__aexit__()
        client.close()
        remote.close()


async def test_tunnel_streams_across_frames_ignores_stderr_and_closes_only_its_connections(monkeypatch):
    engine = DockerSDKEngine(Mock())
    engine.client.api.exec_create.return_value = {"Id": "test-exec"}
    client, remote = socket.socketpair()
    remote.setblocking(False)

    async def attach(api, identity):
        assert identity == "test-exec"
        return client

    monkeypatch.setattr(tunnel, "attach", attach)
    loop = asyncio.get_running_loop()

    async def relay():
        # A diagnostic must never corrupt the HTTP byte stream.
        await loop.sock_sendall(remote, b"\x02\x00\x00\x00" + (10).to_bytes(4, "big") + b"diagnostic")
        while value := await loop.sock_recv(remote, 8192):
            await loop.sock_sendall(remote, b"\x01\x00\x00\x00" + len(value).to_bytes(4, "big") + value)

    task = asyncio.create_task(relay())
    try:
        async with tunnel.DesktopTunnel(engine, "owned-container") as forwarding:
            port = int(forwarding.endpoint.rsplit(":", 1)[1])
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            payload = bytes(range(256)) * 4096

            async def upload():
                for offset in range(0, len(payload), 32768):
                    writer.write(payload[offset : offset + 32768])
                    await writer.drain()

            async with asyncio.timeout(5):
                _, result = await asyncio.gather(upload(), reader.readexactly(len(payload)))
            assert result == payload
            assert len(forwarding._clients) == 1
            # Closing the scope with a live keepalive connection must unblock reads.
        async with asyncio.timeout(2):
            assert await reader.read() == b""
            await task
        writer.close()
        await writer.wait_closed()
        assert not forwarding._clients
        assert client.fileno() == -1
        engine.client.close.assert_not_called()
        options = engine.client.api.exec_create.call_args
        assert options.args[0] == "owned-container"
        assert "TCP4:127.0.0.1:8888,connect-timeout=5" in options.args[1]
        assert options.kwargs["stdin"] is True and options.kwargs["tty"] is False
    finally:
        client.close()
        remote.close()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_tunnel_rejects_excess_connections_without_starting_more_execs(monkeypatch):
    monkeypatch.setattr(tunnel, "_MAX_CONNECTIONS", 1)
    engine = DockerSDKEngine(Mock())
    engine.client.api.exec_create.return_value = {"Id": "test-exec"}
    client, remote = socket.socketpair()
    entered = asyncio.Event()

    async def attach(api, identity):
        entered.set()
        return client

    monkeypatch.setattr(tunnel, "attach", attach)
    try:
        async with tunnel.DesktopTunnel(engine, "owned-container") as forwarding:
            port = int(forwarding.endpoint.rsplit(":", 1)[1])
            first, first_writer = await asyncio.open_connection("127.0.0.1", port)
            await asyncio.wait_for(entered.wait(), 2)
            second, second_writer = await asyncio.open_connection("127.0.0.1", port)
            assert await asyncio.wait_for(second.read(), 2) == b""
            engine.client.api.exec_create.assert_called_once()
            second_writer.close()
            await second_writer.wait_closed()
        assert await asyncio.wait_for(first.read(), 2) == b""
        first_writer.close()
        await first_writer.wait_closed()
    finally:
        client.close()
        remote.close()
