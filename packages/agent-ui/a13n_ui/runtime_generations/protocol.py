"""Bounded private NDJSON process-control protocol."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

CONTROL_PROTOCOL_VERSION = "1"


class ControlProtocolError(Exception):
    """A Runner control peer violated the private protocol."""


class ControlChannel:
    """One serialized bounded process-control connection."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        max_message_bytes: int,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._max_message_bytes = max_message_bytes
        self._lock = asyncio.Lock()

    async def send(self, message_type: str, **fields: object) -> None:
        payload = {"version": CONTROL_PROTOCOL_VERSION, "type": message_type, **fields}
        try:
            encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ControlProtocolError("control message is not serializable") from exc
        if len(encoded) + 1 > self._max_message_bytes:
            raise ControlProtocolError("control message exceeds the configured bound")
        self._writer.write(encoded + b"\n")
        await self._writer.drain()

    async def receive(self, *, expected_type: str | None = None) -> dict[str, Any]:
        try:
            line = await self._reader.readline()
        except (ValueError, asyncio.LimitOverrunError) as exc:
            raise ControlProtocolError("control message exceeds the configured bound") from exc
        if not line:
            raise ControlProtocolError("control connection closed unexpectedly")
        if len(line) > self._max_message_bytes or not line.endswith(b"\n"):
            raise ControlProtocolError("control message exceeds the configured bound")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ControlProtocolError("control message is invalid JSON") from exc
        if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
            raise ControlProtocolError("control message must be a string-keyed object")
        if value.get("version") != CONTROL_PROTOCOL_VERSION:
            raise ControlProtocolError("control protocol version mismatch")
        message_type = value.get("type")
        if not isinstance(message_type, str):
            raise ControlProtocolError("control message type is missing")
        if expected_type is not None and message_type != expected_type:
            raise ControlProtocolError(f"expected {expected_type}, received {message_type}")
        return value

    async def request(self, message_type: str, *, expected_type: str, **fields: object) -> dict[str, Any]:
        async with self._lock:
            await self.send(message_type, **fields)
            return await self.receive(expected_type=expected_type)

    async def close(self) -> None:
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, OSError):
            pass


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate control message field")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite control value: {value}")


def require_string(message: Mapping[str, object], field: str, *, max_length: int = 512) -> str:
    value = message.get(field)
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ControlProtocolError(f"control field {field} is invalid")
    return value


def require_generation(message: Mapping[str, object], expected: str) -> None:
    if require_string(message, "generation_id", max_length=64) != expected:
        raise ControlProtocolError("control message generation mismatch")


def require_string_list(
    message: Mapping[str, object],
    field: str,
    *,
    max_items: int = 64,
    max_item_length: int = 256,
) -> tuple[str, ...]:
    value = message.get(field)
    if not isinstance(value, list) or len(value) > max_items:
        raise ControlProtocolError(f"control field {field} is invalid")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > max_item_length:
            raise ControlProtocolError(f"control field {field} is invalid")
        result.append(item)
    return tuple(result)


__all__ = [
    "CONTROL_PROTOCOL_VERSION",
    "ControlChannel",
    "ControlProtocolError",
    "require_generation",
    "require_string",
    "require_string_list",
]
