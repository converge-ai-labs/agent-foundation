"""Shared file command contract for native remote backends."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import BinaryIO, Protocol

from pydantic import JsonValue

from .models import EnvironmentError, EnvironmentOperationReceipt


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
            f"from ..{module} import {names}",
            files("a13n_harness.providers.environment").joinpath(f"{module}.py").read_text(),
        )
    return source
