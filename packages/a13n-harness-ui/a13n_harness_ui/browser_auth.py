"""Private authentication files for the managed X11 and TigerVNC session."""

from __future__ import annotations

import os
import socket
import struct
import time
from pathlib import Path

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes


def vnc_passwords(control: str, readonly: str) -> bytes:
    # TigerVNC's passwd file uses a public obfuscation key, with reversed DES
    # key bits. Its two eight-byte records distinguish control and view-only.
    key = bytes.fromhex("e84ad660c4721ae0")
    plaintext = b"".join(value.encode("ascii")[:8].ljust(8, b"\0") for value in (control, readonly))
    encoder = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()
    return encoder.update(plaintext) + encoder.finalize()


def xauthority(display_number: str, cookie: bytes) -> bytes:
    # Xauthority FamilyLocal: a length-prefixed record, not a shell command.
    values = (socket.gethostname().encode(), display_number.encode("ascii"), b"MIT-MAGIC-COOKIE-1", cookie)
    return struct.pack("!H", 256) + b"".join(struct.pack("!H", len(value)) + value for value in values)


def write_private(path: Path, content: bytes, *, advance_mtime: bool = False) -> None:
    replacement = path.with_suffix(".new")
    try:
        replacement.touch(mode=0o600, exist_ok=False)
        replacement.write_bytes(content)
        if advance_mtime:
            # x11vnc compares integer mtimes, including on new connections.
            # Rapid handoffs must still publish a strictly newer password file.
            previous = int(path.stat().st_mtime) if path.exists() else 0
            modified = max(int(time.time()), previous + 1)
            os.utime(replacement, (modified, modified))
        replacement.replace(path)
    finally:
        replacement.unlink(missing_ok=True)
