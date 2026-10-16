"""Small raw-RFB test client; bypasses frontend read-only controls deliberately."""

import struct
import time

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect


class AuthRejected(Exception):
    pass


class RFB:
    def __init__(self, endpoint: str, password: str):
        self.buf = b""
        self.s = connect(endpoint, subprotocols=["binary"], open_timeout=15, close_timeout=2, max_size=16 * 1024 * 1024)
        try:
            version = self.read(12)
            assert version.startswith(b"RFB 003.")
            self.send(b"RFB 003.008\n")
            kinds = self.read(self.read(1)[0])
            assert 2 in kinds, ("no VNC auth", list(kinds))
            self.send(b"\x02")
            challenge = self.read(16)
            key = bytes(int(f"{b:08b}"[::-1], 2) for b in password.encode()[:8].ljust(8, b"\x00"))
            enc = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()
            self.send(enc.update(challenge) + enc.finalize())
            if struct.unpack("!I", self.read(4))[0]:
                raise AuthRejected()
            self.send(b"\x01")
            init = self.read(24)
            self.width, self.height = struct.unpack("!HH", init[:4])
            self.read(struct.unpack("!I", init[20:24])[0])
        except BaseException:
            self.close()
            raise

    def send(self, value: bytes) -> None:
        self.s.send(value)

    def read(self, n):
        while len(self.buf) < n:
            b = self.s.recv(timeout=20)
            if not b:
                raise EOFError()
            assert isinstance(b, bytes)
            self.buf += b
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def pointer(self, x, y):
        self.send(struct.pack("!BBHH", 5, 0, x, y))

    def frame(self):
        self.send(struct.pack("!BBH", 2, 0, 1) + struct.pack("!i", 0))
        self.send(struct.pack("!BBHHHH", 3, 0, 0, 0, min(self.width, 64), min(self.height, 64)))
        while True:
            kind = self.read(1)[0]
            if kind == 0:
                break
            if kind == 2:
                continue
            if kind == 3:
                length = struct.unpack("!I", self.read(7)[3:])[0]
                self.read(length)
                continue
            raise AssertionError(kind)
        n = struct.unpack("!H", self.read(3)[1:])[0]
        # Drain raw rectangles so later revocation checks do not mistake buffered frame bytes for a live channel.
        total = 0
        for _ in range(n):
            _x, _y, w, h, enc = struct.unpack("!HHHHi", self.read(12))
            assert enc == 0, enc
            total += len(self.read(w * h * 4))
        return {"width": self.width, "height": self.height, "pixel_bytes": total}

    def closed(self) -> bool:
        try:
            self.s.recv(timeout=4)
            return False
        except TimeoutError:
            return False
        except ConnectionClosed:
            return True

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def wait_cursor(getter, point, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if tuple(getter()) == point:
            return True
        time.sleep(0.1)
    return False
