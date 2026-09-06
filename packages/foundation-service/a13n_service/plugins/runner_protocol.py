"""Bounded private control protocol between a Worker Supervisor and Runner child."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

_PROTOCOL_VERSION = 1
_MAX_MESSAGE_BYTES = 64 * 1024


class PluginRunnerProtocolError(Exception):
    """The private Runner control channel violated its finite contract."""


async def read_runner_message(reader: asyncio.StreamReader) -> dict[str, object]:
    try:
        raw = await reader.readline()
    except (OSError, asyncio.LimitOverrunError, ValueError) as error:
        raise PluginRunnerProtocolError("Runner control read failed") from error
    if not raw or len(raw) > _MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
        raise PluginRunnerProtocolError("Runner control message is invalid")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PluginRunnerProtocolError("Runner control message is invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("version") != _PROTOCOL_VERSION
        or not isinstance(value.get("type"), str)
    ):
        raise PluginRunnerProtocolError("Runner control envelope is invalid")
    return value


async def write_runner_message(writer: asyncio.StreamWriter, message_type: str, **fields: object) -> None:
    if not message_type or len(message_type) > 64:
        raise PluginRunnerProtocolError("Runner control message type is invalid")
    value: dict[str, object] = {"version": _PROTOCOL_VERSION, "type": message_type, **fields}
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as error:
        raise PluginRunnerProtocolError("Runner control message is not serializable") from error
    if len(raw) > _MAX_MESSAGE_BYTES:
        raise PluginRunnerProtocolError("Runner control message exceeds its bound")
    try:
        writer.write(raw)
        await writer.drain()
    except (ConnectionError, OSError) as error:
        raise PluginRunnerProtocolError("Runner control write failed") from error


def require_message_fields(
    message: Mapping[str, object],
    *,
    message_type: str,
    string_fields: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if message.get("type") != message_type:
        raise PluginRunnerProtocolError(f"Expected Runner message {message_type}")
    result: list[str] = []
    for field in string_fields:
        value = message.get(field)
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise PluginRunnerProtocolError(f"Runner message field is invalid: {field}")
        result.append(value)
    return tuple(result)
