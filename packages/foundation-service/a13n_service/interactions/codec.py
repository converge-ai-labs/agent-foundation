"""Strict RFC 8785 encoding for durable Run objects."""

from __future__ import annotations

import json
from typing import Any

import rfc8785
from pydantic import BaseModel, TypeAdapter, ValidationError


class DurableObjectCodecError(ValueError):
    """Durable object bytes are malformed, non-canonical, or schema-invalid."""


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
    except (UnicodeDecodeError, json.JSONDecodeError, DurableObjectCodecError) as error:
        raise DurableObjectCodecError("durable object is not strict UTF-8 JSON") from error
    try:
        canonical = rfc8785.dumps(payload)
    except rfc8785.CanonicalizationError as error:
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


__all__ = ["DurableObjectCodecError", "canonical_model_bytes", "decode_canonical_model"]
