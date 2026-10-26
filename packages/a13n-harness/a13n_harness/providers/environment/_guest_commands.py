"""Shared file command contract for native remote backends."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import BinaryIO, Protocol

from pydantic import JsonValue

from a13n_harness.environment.models import EnvironmentError, EnvironmentOperationReceipt


class FileConfiguration(Protocol):
    @property
    def max_file_bytes(self) -> int: ...


class FileCommands(Protocol):
    @property
    def configuration(self) -> FileConfiguration: ...
    async def files(
        self, action: str, arguments: dict[str, JsonValue], *, mutation: bool = False
    ) -> dict[str, JsonValue]: ...
    def read_stream(self, path: str) -> AsyncIterator[bytes]: ...
    async def write_stream(self, path: str, source: BinaryIO) -> None: ...
    async def read_range(self, path: str, offset: int, length: int) -> bytes: ...
    def receipt(self) -> EnvironmentOperationReceipt: ...


def decoded_bytes(value: dict[str, JsonValue]) -> bytes:
    data = value.get("data")
    if not isinstance(data, str):
        raise EnvironmentError("Guest returned invalid bytes.", code="environment_provider_failure")
    try:
        return base64.b64decode(data, validate=True)
    except ValueError:
        raise EnvironmentError("Guest returned invalid bytes.", code="environment_provider_failure") from None


def file_helper_source() -> str:
    from importlib.resources import files

    source = files("a13n_harness.providers.environment").joinpath("_guest", "files.py").read_text()
    for module, names in (
        ("_file_patterns", "PathPattern, PatternError, content_pattern"),
        ("_file_search", "search_text_file"),
    ):
        source = source.replace(
            f"from a13n_harness.providers.environment.{module} import {names}",
            files("a13n_harness.providers.environment").joinpath(f"{module}.py").read_text(),
        )
    return source.replace(
        "from a13n_harness.environment._zip import extract_zip",
        files("a13n_harness.environment").joinpath("_zip.py").read_text(),
    )


async def collect_range(stream: AsyncIterator[bytes], offset: int, length: int) -> bytes:
    """Slice one raw download with bounded memory and close early on completion."""
    from a13n_harness.environment._streams import closing_stream

    result = bytearray()
    async with closing_stream(stream) as chunks:
        async for chunk in chunks:
            if offset >= len(chunk):
                offset -= len(chunk)
                continue
            selected = chunk[offset : offset + length - len(result)]
            offset = 0
            result.extend(selected)
            if len(result) == length:
                break
    return bytes(result)
