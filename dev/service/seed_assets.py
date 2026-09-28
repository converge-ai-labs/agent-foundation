"""Small valid files covering the text, image, audio, document, archive and download views, stored as assets."""

from __future__ import annotations

import io
import json
import struct
import wave
import zipfile
import zlib
from dataclasses import dataclass

from dev.service.api import Api, Json


@dataclass(frozen=True, slots=True)
class Example:
    name: str
    content_type: str
    data: bytes


BRIEF = """# Fictional release brief

Project: Orbit documentation assistant

Prepare a release review covering navigation, conversation history, attachments and recovery from an
intentional model failure. No real customer data is included.

中文备注：检查长文件名、混合语言与多行文本的显示效果。
""".encode()  # noqa: RUF001


def examples() -> tuple[Example, ...]:
    return (
        Example("Release brief.md", "text/markdown", BRIEF),
        Example(
            "Team metrics.csv", "text/csv", "team,completed,notes\nAtlas,42,fictional\n演示团队,0,empty\n".encode()
        ),
        Example("Review result.json", "application/json", json.dumps({"fictional": True, "results": [1, 2]}).encode()),
        Example("Preview swatches.png", "image/png", png()),
        Example("One second of silence.wav", "audio/wav", _silence()),
        Example("Release checklist.pdf", "application/pdf", _pdf()),
        Example("Project notes.zip", "application/zip", _archive()),
        Example("Empty document.txt", "text/plain", b""),
        Example(
            "Long scroll document.txt",
            "text/plain",
            "A fictional paragraph for scrolling. 中文内容。\n".encode() * 4000,
        ),
        Example("Unknown format.bin", "application/octet-stream", bytes(range(256)) * 4),
        Example(
            "A deliberately long file name with spaces and Unicode, 测试文件名称截断.txt",
            "text/plain",
            b"Long file name fixture.\n",
        ),
        Example("Literal markup.html", "text/html", b"<!doctype html><title>Fictional</title><p>Download only.</p>"),
        Example("Sample code.py", "text/x-python", b'print("Local fixture only")\n'),
    )


def upload(api: Api, name: str, content_type: str, data: bytes) -> str:
    """Stage bytes; returns the upload ID."""
    return api.post("/api/v1/uploads", files={"file": (name, data, content_type)}, idempotent=True)["upload_id"]


def store(api: Api, example: Example) -> Json:
    upload_id = upload(api, example.name, example.content_type, example.data)
    return api.post("/api/v1/assets", {"upload_id": upload_id, "name": example.name})


def png() -> bytes:
    """A 64x64 gradient, the seed's one image: icons, avatars and an attachment."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    size = 64
    pixels = b"".join(b"\x00" + bytes((40, 110 + row, 190)) * size for row in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def _silence() -> bytes:
    sound = io.BytesIO()
    with wave.open(sound, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(8000)
        target.writeframes(b"\x00\x00" * 8000)
    return sound.getvalue()


def _pdf() -> bytes:
    """One page of text, with the cross-reference offsets readers check."""
    text = b"BT /F1 18 Tf 72 720 Td (Fictional release checklist) Tj ET"
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(text), text),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    )
    document = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    table = len(document)
    document += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    document += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    document += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, table)
    return bytes(document)


def _archive() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for name, text in (
            ("README.md", "# Fictional release brief\n"),
            ("notes/decision.txt", "No real project or customer data.\n"),
        ):
            # A fixed timestamp keeps the bytes, and so the digest, identical on every call.
            target.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), text)
    return output.getvalue()
