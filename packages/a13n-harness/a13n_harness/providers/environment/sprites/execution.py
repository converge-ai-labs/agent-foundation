"""Sprites execution implementation."""

from collections.abc import AsyncIterator
from typing import BinaryIO

from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.providers.environment.native.environment import NativeExecution
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.transfers import (
    CHUNK_BYTES,
    SPRITES_UPLOAD_BYTES,
    file_chunks,
    remaining_bytes,
)
from a13n_harness.providers.environment.sprites.commands import command
from a13n_harness.providers.environment.sprites.shared import (
    SpritesEnvironmentConfiguration,
    SpritesReference,
    SpritesTargetState,
)


class SpritesExecution(SpritesReference, NativeExecution[SpritesEnvironmentConfiguration, SpritesTargetState]):
    async def open(self, *, execution_id: str) -> None:
        self.require_target()
        info = await self.lookup()
        if info is None:
            raise failure(self.provider_key, "provider_target_missing", Category.MISSING)
        await self.open_operations(self.execute, info.id, execution_id)

    async def read_stream(self, path: str) -> AsyncIterator[bytes]:
        async with self.transport().stream(
            "GET",
            self.path + "/fs/read",
            params={"path": path, "workingDir": self.config.root},
        ) as response:
            async for chunk in response.aiter_bytes(CHUNK_BYTES):
                yield chunk

    async def write_stream(self, path: str, source: BinaryIO, *, mode: int) -> None:
        size = remaining_bytes(source)
        if size > SPRITES_UPLOAD_BYTES:
            raise EnvironmentError("Sprites uploads are limited to 16 MiB.", code="environment_too_large")
        async with self.transport().stream(
            "PUT",
            self.path + "/fs/write",
            params={"path": path, "workingDir": self.config.root, "mode": f"{mode:04o}"},
            headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)},
            content=file_chunks(source),
        ) as response:
            # Drain only a bounded acknowledgement, never buffer an arbitrary body.
            total = 0
            async for chunk in response.aiter_bytes(CHUNK_BYTES):
                total += len(chunk)
                if total > CHUNK_BYTES:
                    raise EnvironmentError("Invalid upload response.", code="environment_provider_failure")

    async def execute(self, argv: list[str], timeout: float) -> str:
        return await command(self, argv, timeout)
