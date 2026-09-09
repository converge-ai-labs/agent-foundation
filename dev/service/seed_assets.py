"""Small valid public files covering text, image, audio, archive, and download views."""

import io
import json
import struct
import wave
import zipfile
import zlib
from pathlib import Path

FIXTURES = Path(__file__).with_name("fixtures")


def asset_examples() -> list[tuple[str, str, bytes]]:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
        target.writestr("README.md", "# Fictional release brief\nPublic local archive fixture.\n")
        target.writestr("notes/decision.txt", "No real project or customer data.\n")
    sound = io.BytesIO()
    with wave.open(sound, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(8000)
        target.writeframes(b"\x00\x00" * 8000)
    return [
        ("Release brief — 产品评审.md", "text/markdown", (FIXTURES / "brief.md").read_bytes()),
        (
            "Fictional metrics.csv",
            "text/csv",
            "team,completed,notes\nAtlas,42,fictional\n演示团队,0,empty backlog\n".encode(),
        ),
        (
            "Review result.json",
            "application/json",
            json.dumps({"fictional": True, "results": [1, 2], "note": None}).encode(),
        ),
        ("Preview swatches.png", "image/png", _png()),
        ("One second of silence.wav", "audio/wav", sound.getvalue()),
        ("Project notes.zip", "application/zip", archive.getvalue()),
        ("Empty document.txt", "text/plain", b""),
        (
            "Large scroll document.txt",
            "text/plain",
            ("Public fictional paragraph for scrolling. 中文内容。\n" * 16000).encode(),
        ),
        ("Unknown format.bin", "application/octet-stream", bytes(range(256)) * 4),
        (
            "A deliberately long filename with spaces and Unicode — 测试文件用于检查附件名称截断.txt",
            "text/plain",
            b"Fictional release brief\nLong filename fixture.\n",
        ),
        (
            "Literal markup.html",
            "text/html",
            b"<!doctype html><title>Fictional fixture</title><p>Download-only sample &amp; literal markup.</p>",
        ),
        ("Sample code.py", "text/x-python", b'# Fictional release brief\nprint("Local fixture only")\n'),
    ]


def _png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    size = 64
    pixels = b"".join(b"\x00" + bytes((40, 110 + y, 190)) * size for y in range(size))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )
