from __future__ import annotations

import hashlib
import struct
import threading

import anyio
import pytest
import zstandard
from a13n_service.storage import codec
from a13n_service.storage.codec import (
    DurableObjectCodecError,
    DurableObjectSizeError,
    canonical_model_bytes,
    compressed_size_limit,
    decode_compressed_model,
    encode_compressed_model,
    run_codec,
)
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

pytestmark = pytest.mark.anyio


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: JsonValue


ADAPTER = TypeAdapter(Document)


def _frame(body: bytes, **options) -> bytes:
    return zstandard.ZstdCompressor(level=1, write_checksum=True, **options).compress(body)


async def _decode(body: bytes, *, max_bytes: int = 8192) -> Document:
    return await decode_compressed_model(
        body, ADAPTER, max_bytes=max_bytes, digest_sha256=hashlib.sha256(body).hexdigest()
    )


@pytest.mark.parametrize("value", ["", "state " * 1000, {"unicode": "状态", "number": 1e-7, "null": None}])
async def test_compressed_model_round_trip_preserves_canonical_json(value: JsonValue) -> None:
    document = Document(value=value)
    canonical = canonical_model_bytes(document)
    body, digest = await encode_compressed_model(document, max_bytes=len(canonical))

    frame = zstandard.get_frame_parameters(body)
    assert frame.has_checksum and frame.content_size == len(canonical) and frame.dict_id == 0
    assert len(body) <= compressed_size_limit(len(canonical))
    assert zstandard.ZstdDecompressor().decompress(body, allow_extra_data=False) == canonical
    assert digest == hashlib.sha256(body).hexdigest()
    assert await _decode(body, max_bytes=len(canonical)) == document


async def test_tiny_document_can_expand_without_exceeding_the_decoded_limit() -> None:
    document = Document(value=1)
    canonical = canonical_model_bytes(document)
    body, _ = await encode_compressed_model(document, max_bytes=len(canonical))

    assert len(body) > len(canonical)
    assert await _decode(body, max_bytes=len(canonical)) == document


async def test_decoded_limit_applies_on_write_and_read() -> None:
    document = Document(value="compressible " * 500)
    canonical = canonical_model_bytes(document)
    body, _ = await encode_compressed_model(document, max_bytes=len(canonical))
    assert len(body) < len(canonical) - 1

    with pytest.raises(DurableObjectSizeError, match="decoded size"):
        await encode_compressed_model(document, max_bytes=len(canonical) - 1)
    with pytest.raises(DurableObjectSizeError, match="decoded size"):
        await _decode(body, max_bytes=len(canonical) - 1)


@pytest.mark.parametrize(
    "corruption",
    [
        "json",
        "no_checksum",
        "unknown_size",
        "dictionary",
        "truncated_header",
        "truncated_body",
        "checksum",
        "extra",
        "frames",
        "skippable",
    ],
)
async def test_rejects_invalid_or_incomplete_frames(corruption: str) -> None:
    canonical = b'{"value":"hello"}'
    body = _frame(canonical)
    match corruption:
        case "json":
            body = canonical
        case "no_checksum":
            body = zstandard.ZstdCompressor().compress(canonical)
        case "unknown_size":
            body = _frame(canonical, write_content_size=False)
        case "dictionary":
            # One-byte dictionary ID precedes the content-size field.
            body = body[:4] + bytes([body[4] | 1]) + b"\x01" + body[5:]
            assert zstandard.get_frame_parameters(body).dict_id == 1
        case "truncated_header":
            body = body[:5]
        case "truncated_body":
            body = body[:-1]
        case "checksum":
            body = body[:-1] + bytes([body[-1] ^ 1])
        case "extra":
            body += b"trailing bytes"
        case "frames":
            body += _frame(canonical)
        case "skippable":
            body = struct.pack("<II", 0x184D2A50, 0) + body

    # Recompute SHA-256 so framing, not only object identity, must reject it.
    with pytest.raises(DurableObjectCodecError):
        await _decode(body)


@pytest.mark.parametrize(
    "canonical",
    [
        b'{"value": 1}',
        b'{"value":1,"value":2}',
        b'{"value":NaN}',
        b'{"value":"\xff"}',
        b'{"wrong":1}',
        b'{"value":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}",
    ],
)
async def test_compression_does_not_bypass_strict_json_or_schema_checks(canonical: bytes) -> None:
    with pytest.raises(DurableObjectCodecError):
        await _decode(_frame(canonical))


@pytest.mark.parametrize("invalid", ["digest", "encoded_size", "decoded_size", "window"])
async def test_rejects_identity_and_resource_violations_before_decompression(monkeypatch, invalid: str) -> None:
    canonical = canonical_model_bytes(Document(value="a" * 2048))
    body = _frame(canonical)
    limit = len(canonical)
    digest = hashlib.sha256(body).hexdigest()
    if invalid == "digest":
        digest = "0" * 64
    elif invalid == "encoded_size":
        body += b"a" * compressed_size_limit(limit)
        digest = hashlib.sha256(body).hexdigest()
    elif invalid == "decoded_size":
        limit -= 1
    else:
        # Turn the single-segment frame into a frame declaring a 32 MiB window.
        body = body[:4] + bytes([body[4] & ~0x20]) + b"\x78" + body[5:]
        frame = zstandard.get_frame_parameters(body)
        assert frame.content_size == len(canonical) and frame.window_size > limit
        digest = hashlib.sha256(body).hexdigest()

    def unexpected_decompression(*args, **kwargs):
        pytest.fail("invalid object reached decompression allocation")

    monkeypatch.setattr(zstandard, "ZstdDecompressor", unexpected_decompression)
    with pytest.raises(DurableObjectCodecError):
        await decode_compressed_model(body, ADAPTER, max_bytes=limit, digest_sha256=digest)


async def test_serialization_and_schema_validation_run_outside_event_loop(monkeypatch) -> None:
    loop_thread = threading.get_ident()
    operations: list[str] = []
    encode = codec.canonical_model_bytes
    decode = codec.decode_canonical_model

    def encode_on_worker(*args):
        assert threading.get_ident() != loop_thread
        operations.append("encode")
        return encode(*args)

    def decode_on_worker(*args):
        assert threading.get_ident() != loop_thread
        operations.append("decode")
        return decode(*args)

    monkeypatch.setattr(codec, "canonical_model_bytes", encode_on_worker)
    monkeypatch.setattr(codec, "decode_canonical_model", decode_on_worker)
    document = Document(value="hello")
    body, _ = await encode_compressed_model(document, max_bytes=1024)
    assert await _decode(body) == document
    assert operations == ["encode", "decode"]


async def test_cancellation_keeps_running_codec_slots_and_stops_follow_up_work() -> None:
    started = anyio.Event()
    queued = anyio.Event()
    release = threading.Event()
    scopes: dict[int, anyio.CancelScope] = {}
    executing: list[int] = []
    completed: list[int] = []

    def record_start(index: int) -> None:
        executing.append(index)
        if len(executing) == 2:
            started.set()

    def blocked(index: int) -> None:
        anyio.from_thread.run_sync(record_start, index)
        assert release.wait(10)

    async def work(index: int) -> None:
        with anyio.CancelScope() as scope:
            scopes[index] = scope
            if index == 2:
                queued.set()
            await run_codec(blocked, index)
            completed.append(index)

    with anyio.fail_after(10):
        async with anyio.create_task_group() as tasks:
            try:
                tasks.start_soon(work, 0)
                tasks.start_soon(work, 1)
                await started.wait()
                tasks.start_soon(work, 2)
                await queued.wait()
                scopes[0].cancel()
                scopes[2].cancel()
                await anyio.sleep(0)
                assert set(executing) == {0, 1}
            finally:
                release.set()

    assert set(executing) == {0, 1}
    assert completed == [1]
