"""POSIX stdio transport and disconnect handling for ACP."""

from __future__ import annotations

import asyncio
import os
import select
import sys
from collections.abc import AsyncIterator, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from typing import IO, SupportsIndex, cast

from acp import Agent
from acp.agent import AgentSideConnection

from a13n_harness_ui.acp_server import AcpAgent
from a13n_harness_ui.app import HarnessUiApp


class _StdinReader(asyncio.StreamReader):
    def __init__(self) -> None:
        # StreamReader pauses above twice this pending-input limit. The SDK
        # can still consume larger individual frames in chunks.
        super().__init__(limit=1024 * 1024)
        self._read_changed = asyncio.Event()
        self._eof_received = False
        self._pipe: asyncio.ReadTransport | None = None

    def set_transport(self, transport: asyncio.BaseTransport) -> None:
        super().set_transport(transport)
        self._pipe = cast(asyncio.ReadTransport, transport)

    def feed_data(self, data: Iterable[SupportsIndex]) -> None:
        super().feed_data(data)
        if self._pipe is not None and not self._pipe.is_reading():
            self._read_changed.set()

    def feed_eof(self) -> None:
        super().feed_eof()
        self._eof_received = True
        self._read_changed.set()

    async def wait_disconnected(self, stdin_fd: int) -> None:
        # Poll only while asyncio has paused reads. POLLHUP reports a closed
        # writer even with unread bytes in the pipe, without consuming them.
        hangup = select.poll()
        # Linux can report a socket peer's write-side shutdown as RDHUP
        # without HUP if that peer is still reading.
        hangup.register(stdin_fd, select.POLLHUP | getattr(select, "POLLRDHUP", 0))
        while not self._eof_received:
            if self._pipe is not None and not self._pipe.is_reading():
                try:
                    if hangup.poll(0):
                        return
                except OSError:
                    return
                await asyncio.sleep(0.05)
            else:
                await self._read_changed.wait()
                self._read_changed.clear()


@asynccontextmanager
async def _stdio(protocol: IO[str]) -> AsyncIterator[tuple[_StdinReader, asyncio.StreamWriter]]:
    loop = asyncio.get_running_loop()
    reader = _StdinReader()
    input_pipe, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    try:
        # asyncio's pipe flow control wakes drain() when abort() closes the
        # connection. The SDK's stdout protocol only wakes on resume_writing().
        unused_reader = asyncio.StreamReader()
        output_protocol = asyncio.StreamReaderProtocol(unused_reader)
        output_pipe, _ = await loop.connect_write_pipe(lambda: output_protocol, protocol)
        try:
            yield reader, asyncio.StreamWriter(output_pipe, output_protocol, None, loop)
        finally:
            if not output_pipe.is_closing():
                output_pipe.abort()
    finally:
        input_pipe.close()


async def _listen(connection: AgentSideConnection, reader: _StdinReader) -> None:
    # A receive can be waiting to send a parse-error response. Observe
    # stdin closing even if the SDK listener cannot read more bytes.
    listener = asyncio.create_task(connection.listen())
    disconnect = asyncio.create_task(reader.wait_disconnected(sys.stdin.fileno()))
    try:
        await asyncio.wait((listener, disconnect), return_when=asyncio.FIRST_COMPLETED)
        if listener.done():
            listener.result()
        if disconnect.done():
            disconnect.result()
    finally:
        for task in (listener, disconnect):
            task.cancel()
        await asyncio.gather(listener, disconnect, return_exceptions=True)


async def serve(app_factory: AbstractAsyncContextManager[HarnessUiApp], *, unattended: bool = False) -> None:
    """Keep even subprocess and extension diagnostics away from the protocol descriptor."""
    sys.stdout.flush()
    with os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8") as original_stdout:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        try:
            with os.fdopen(os.dup(original_stdout.fileno()), "w", encoding="utf-8") as protocol:
                async with _stdio(protocol) as (reader, writer):
                    closing = None
                    try:
                        async with app_factory as app:
                            agent = AcpAgent(app, unattended=unattended)
                            # Own the listen/close boundary without changing SDK routing.
                            connection = AgentSideConnection(cast(Agent, agent), writer, reader, listening=False)
                            try:
                                await _listen(connection, reader)
                            finally:
                                agent.disconnect()
                                if not writer.is_closing():
                                    writer.transport.abort()
                                # Exit the App context without waiting for SDK handlers.
                                closing = asyncio.create_task(connection.close())
                    finally:
                        if closing is not None:
                            # An aborted writer can report connection loss when
                            # SDK close joins its sender. The client is gone.
                            with suppress(ConnectionError):
                                await closing
        finally:
            sys.stdout.flush()
            os.dup2(original_stdout.fileno(), sys.stdout.fileno())
