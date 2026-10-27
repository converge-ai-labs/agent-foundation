import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_harness_ui.browser_vnc import VncConnection

pytestmark = pytest.mark.anyio


async def test_linux_transport_relays_native_rfb_and_stops_input_after_close():
    reader = asyncio.StreamReader()
    reader.feed_data(b"native framebuffer")
    writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock(), is_closing=Mock(return_value=False))
    connection = VncConnection(reader, writer)
    assert await connection.read(65536) == b"native framebuffer"
    await connection.send(b"native input")
    writer.write.assert_called_once_with(b"native input")
    connection.close()
    writer.close.assert_called_once()
    writer.is_closing.return_value = True
    await connection.send(b"revoked")
    writer.write.assert_called_once()
    await connection.wait_closed()
    writer.wait_closed.assert_awaited_once()


async def test_peer_reset_is_already_closed():
    writer = Mock(wait_closed=AsyncMock(side_effect=ConnectionResetError()))
    await VncConnection(asyncio.StreamReader(), writer).wait_closed()
