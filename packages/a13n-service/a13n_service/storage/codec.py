"""Canonical JSON and bounded compressed encoding for durable objects."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

import rfc8785
import zstandard
from anyio import CapacityLimiter, to_thread
from anyio.lowlevel import RunVar, checkpoint_if_cancelled
from pydantic import BaseModel, TypeAdapter, ValidationError

COMPRESSED_JSON_CONTENT_TYPE = "application/zstd"
COMPRESSED_JSON_ENCODING = "rfc8785-zstd-v1"

# Share two transform slots across all Stores in an event loop. A state can
# already occupy 256 MiB before parsed values and canonicalization copies.
_codec_limiter = RunVar[CapacityLimiter]("a13n_object_codec_limiter")


class DurableObjectCodecError(ValueError):
    """Durable object bytes are malformed, non-canonical, or schema-invalid."""


class DurableObjectSizeError(DurableObjectCodecError):
    """An encoded body or its decoded value exceeds the storage bound."""


def compressed_size_limit(max_bytes: int) -> int:
    """Allow Zstandard's worst-case single-frame overhead within a finite bound."""

    if max_bytes < 1:
        raise ValueError("decoded size limit must be positive")
    return max_bytes + max_bytes // 256 + 64


async def run_codec[T](operation: Callable[..., T], *args: Any) -> T:
    """Run serialization or validation under the shared transform budget."""

    limiter = _codec_limiter.get(None)
    if limiter is None:
        limiter = CapacityLimiter(2)
        _codec_limiter.set(limiter)
    # Keep the slot until the worker finishes, even if its caller is cancelled.
    result = await to_thread.run_sync(operation, *args, limiter=limiter)
    await checkpoint_if_cancelled()
    return result


async def encode_compressed_model(value: BaseModel, *, max_bytes: int) -> tuple[bytes, str]:
    """Return the exact encoded body and its SHA-256 publication identity."""

    return await run_codec(_encode_compressed_model, value, max_bytes)


def _encode_compressed_model(value: BaseModel, max_bytes: int) -> tuple[bytes, str]:
    encoded_limit = compressed_size_limit(max_bytes)
    canonical = canonical_model_bytes(value)
    if len(canonical) > max_bytes:
        raise DurableObjectSizeError("durable object exceeds its decoded size limit")
    body = zstandard.ZstdCompressor(level=1, write_checksum=True, write_content_size=True).compress(canonical)
    if len(body) > encoded_limit:
        raise DurableObjectSizeError("durable object exceeds its encoded size limit")
    return body, hashlib.sha256(body).hexdigest()


async def decode_compressed_model[T](
    body: bytes,
    adapter: TypeAdapter[T],
    *,
    max_bytes: int,
    digest_sha256: str,
) -> T:
    """Verify encoded identity, bounded framing, and strict canonical schema."""

    return await run_codec(_decode_compressed_model, body, adapter, max_bytes, digest_sha256)


def _decode_compressed_model[T](body: bytes, adapter: TypeAdapter[T], max_bytes: int, digest_sha256: str) -> T:
    if len(body) > compressed_size_limit(max_bytes):
        raise DurableObjectSizeError("durable object exceeds its encoded size limit")
    if hashlib.sha256(body).hexdigest() != digest_sha256:
        raise DurableObjectCodecError("durable object digest does not match its encoded body")
    if not body.startswith(zstandard.FRAME_HEADER):
        raise DurableObjectCodecError("durable object is not a standard Zstandard frame")
    try:
        frame = zstandard.get_frame_parameters(body)
        if (
            not frame.has_checksum
            or frame.dict_id
            or frame.content_size in {zstandard.CONTENTSIZE_UNKNOWN, zstandard.CONTENTSIZE_ERROR}
        ):
            raise DurableObjectCodecError("durable object requires a checksum, known content size, and no dictionary")
        if frame.content_size > max_bytes or frame.window_size > max_bytes:
            raise DurableObjectSizeError("durable object exceeds its decoded size or window limit")
        # Check the frame first: max_output_size alone does not constrain an
        # allocation when the frame already declares its own content size.
        canonical = zstandard.ZstdDecompressor().decompress(body, max_output_size=max_bytes, allow_extra_data=False)
    except zstandard.ZstdError as error:
        raise DurableObjectCodecError("durable object is not a complete checksummed Zstandard frame") from error
    if len(canonical) != frame.content_size:
        raise DurableObjectCodecError("durable object decoded size does not match its frame")
    return decode_canonical_model(canonical, adapter)


def canonical_model_bytes(value: BaseModel) -> bytes:
    payload = value.model_dump(mode="json", by_alias=True, exclude_none=False)
    try:
        return rfc8785.dumps(payload)
    except rfc8785.CanonicalizationError as error:
        raise DurableObjectCodecError("durable object is not RFC 8785 canonicalizable") from error


def decode_canonical_model[T](body: bytes, adapter: TypeAdapter[T]) -> T:
    try:
        payload = json.loads(
            body,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, DurableObjectCodecError) as error:
        raise DurableObjectCodecError("durable object is not strict UTF-8 JSON") from error
    try:
        canonical = rfc8785.dumps(payload)
    except (rfc8785.CanonicalizationError, RecursionError) as error:
        raise DurableObjectCodecError("durable object is not RFC 8785 canonicalizable") from error
    if canonical != body:
        raise DurableObjectCodecError("durable object bytes are not RFC 8785 canonical JSON")
    try:
        return adapter.validate_python(payload)
    except ValidationError as error:
        raise DurableObjectCodecError("durable object does not match its declared schema") from error


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DurableObjectCodecError("durable object contains a duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise DurableObjectCodecError(f"durable object contains invalid JSON number {value}")


__all__ = [
    "COMPRESSED_JSON_CONTENT_TYPE",
    "COMPRESSED_JSON_ENCODING",
    "DurableObjectCodecError",
    "DurableObjectSizeError",
    "canonical_model_bytes",
    "compressed_size_limit",
    "decode_canonical_model",
    "decode_compressed_model",
    "encode_compressed_model",
    "run_codec",
]
