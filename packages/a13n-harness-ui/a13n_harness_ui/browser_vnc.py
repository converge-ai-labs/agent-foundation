"""Viewer connections: native private VNC and an authenticated Screen Sharing bridge."""

from __future__ import annotations

import asyncio
import struct

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes

VERSION = b"RFB 003.008\n"
MAX_TEXT = 262144
# Rendering, cursor, desktop notifications, fences and clipboard supported by noVNC.
# Do not negotiate power control, physical desktop resizing or alternate input formats.
ENCODINGS = {
    value & 0xFFFFFFFF
    for value in {
        0,
        1,
        2,
        5,
        6,
        7,
        16,
        21,
        50,
        -260,
        -223,
        -224,
        -239,
        -261,
        -307,
        -312,
        -313,
        0x574D5664,
        0xC0A1E5CE,
        *range(-32, -22),
        *range(-256, -246),
    }
}


def password_bytes(password: str) -> bytes:
    try:
        value = password.encode("latin-1")
    except UnicodeEncodeError:
        raise ValueError("Screen Sharing VNC password must contain 1-8 Latin-1 characters") from None
    if not 1 <= len(value) <= 8 or b"\0" in value:
        raise ValueError("Screen Sharing VNC password must contain 1-8 non-NUL Latin-1 characters")
    return value


def challenge_response(password: str, challenge: bytes) -> bytes:
    key = bytes(int(f"{byte:08b}"[::-1], 2) for byte in password_bytes(password).ljust(8, b"\0"))
    encryptor = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()
    return encryptor.update(challenge) + encryptor.finalize()


class VncConnection:
    """A single revocable viewer transport. Linux enforces input authority natively."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer = reader, writer

    async def read(self, size: int) -> bytes:
        return await self.reader.read(size)

    async def send(self, data: bytes) -> None:
        if self.is_closing():
            return
        self.writer.write(data)
        await self.writer.drain()

    def is_closing(self) -> bool:
        return self.writer.is_closing()

    def close(self) -> None:
        self.writer.close()

    async def wait_closed(self) -> None:
        try:
            await self.writer.wait_closed()
        except ConnectionError:
            # A peer reset is already a closed transport, not an input lease.
            pass


async def screen_sharing(password: str, *, controlling: bool) -> ScreenSharingConnection:
    """Authenticate only to the local macOS service; never disclose its password."""
    password_bytes(password)
    async with asyncio.timeout(10):
        reader, writer = await asyncio.open_connection("127.0.0.1", 5900)
        try:
            banner = await reader.readexactly(12)
            if banner not in {VERSION, b"RFB 003.889\n"}:
                raise OSError("Screen Sharing must support RFB 3.8")
            writer.write(VERSION)
            count = (await reader.readexactly(1))[0]
            types = await reader.readexactly(count)
            if 2 not in types:
                raise OSError("Enable 'VNC viewers may control screen with password' in Screen Sharing settings")
            writer.write(b"\x02")
            writer.write(challenge_response(password, await reader.readexactly(16)))
            await writer.drain()
            if await reader.readexactly(4) != b"\0\0\0\0":
                raise OSError("Screen Sharing rejected the configured VNC password")
            # Never let a viewer disconnect other users of the native desktop.
            writer.write(b"\x01")
            await writer.drain()
            header = await reader.readexactly(24)
            length = int.from_bytes(header[20:24])
            if length > 65536:
                raise OSError("Screen Sharing desktop name is too long")
            init = header + await reader.readexactly(length)
            return ScreenSharingConnection(reader, writer, init, controlling=controlling)
        except BaseException:
            writer.close()
            await writer.wait_closed()
            raise


class ScreenSharingConnection(VncConnection):
    """Terminate RFB authentication and enforce viewer authority on complete messages.

    Framebuffer bytes remain native RFB. Only the small client-message vocabulary
    is parsed, independently of WebSocket frame boundaries.
    """

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, init: bytes, *, controlling: bool
    ) -> None:
        super().__init__(reader, writer)
        self.init = init
        self.controlling = controlling
        self.pending = bytearray()
        self.phase = 0
        self.output: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.output.put_nowait(VERSION)
        self.streaming = False
        self.keys: set[int] = set()
        self.pointer: bytes | None = None

    async def read(self, size: int) -> bytes:
        if not self.streaming:
            output = await self.output.get()
            if output is not None:
                return output
            self.streaming = True
        return await super().read(size)

    async def send(self, data: bytes) -> None:
        if self.is_closing():
            return
        self.pending.extend(data)
        while self.pending:
            if self.phase < 3:
                length = 12 if self.phase == 0 else 1
                if len(self.pending) < length:
                    break
                message = bytes(self.pending[:length])
                del self.pending[:length]
                if self.phase == 0:
                    if message != VERSION:
                        raise OSError("Viewer must use RFB 3.8")
                    self.output.put_nowait(b"\x01\x01")
                elif self.phase == 1:
                    if message != b"\x01":
                        raise OSError("Invalid viewer security selection")
                    # Admission already authenticated this WebSocket.
                    self.output.put_nowait(b"\0\0\0\0")
                else:
                    self.output.put_nowait(self.init)
                    self.output.put_nowait(None)
                self.phase += 1
                continue
            length = self.message_length()
            if length is None or len(self.pending) < length:
                break
            message = bytes(self.pending[:length])
            del self.pending[:length]
            self.forward(message)
        await self.writer.drain()

    def message_length(self) -> int | None:
        data = self.pending
        kind = data[0]
        fixed = {0: 20, 3: 10, 4: 8, 5: 6, 150: 10}
        if kind in fixed:
            if kind == 5 and len(data) >= 2 and data[1] & 128:
                raise OSError("Extended pointer input was not negotiated")
            return fixed[kind]
        if kind == 2:
            if len(data) < 4:
                return None
            count = int.from_bytes(data[2:4])
            if count > 4096:
                raise OSError("Too many VNC encodings")
            return 4 + 4 * count
        if kind == 6:
            if len(data) < 8:
                return None
            length = abs(int.from_bytes(data[4:8], signed=True))
            if length > MAX_TEXT:
                raise OSError("VNC clipboard exceeds the supported size")
            return 8 + length
        if kind == 248:
            if len(data) < 9:
                return None
            if data[8] > 64:
                raise OSError("Invalid VNC fence length")
            return 9 + data[8]
        raise OSError(f"Unsupported VNC client message: {kind}")

    def forward(self, message: bytes) -> None:
        kind = message[0]
        if kind == 2:
            values = [value for (value,) in struct.iter_unpack(">I", message[4:]) if value in ENCODINGS]
            message = struct.pack(">BBH", 2, 0, len(values)) + b"".join(struct.pack(">I", value) for value in values)
        elif kind in {4, 5, 6}:
            if not self.controlling:
                return
            if kind == 4:
                key = int.from_bytes(message[4:8])
                if message[1]:
                    self.keys.add(key)
                else:
                    self.keys.discard(key)
            elif kind == 5:
                self.pointer = message[2:6] if message[1] else None
        self.writer.write(message)

    def close(self) -> None:
        if self.is_closing():
            return
        self.controlling = False
        self.pending.clear()
        # Queue releases before transport.close(), which flushes buffered writes.
        # wait_closed is a transport completion, not a native input acknowledgement.
        for key in self.keys:
            self.writer.write(struct.pack(">BBHI", 4, 0, 0, key))
        self.keys.clear()
        if self.pointer is not None:
            self.writer.write(b"\x05\0" + self.pointer)
            self.pointer = None
        self.output.put_nowait(b"")
        super().close()
