"""Native Modal Files & Shell over bounded SDK exec streams, without a guest daemon."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import BinaryIO

from a13n_harness.environment._streams import closing_stream
from a13n_harness.environment.errors import EnvironmentProviderErrorCategory as Category
from a13n_harness.environment.errors import EnvironmentProviderOutcomeCertainty as Certainty
from a13n_harness.providers.environment.modal.shared import (
    ModalEnvironmentConfiguration,
    ModalReference,
    ModalState,
    execute_command,
    sdk_errors,
)
from a13n_harness.providers.environment.native.environment import NativeExecution
from a13n_harness.providers.environment.native.errors import failure
from a13n_harness.providers.environment.native.transfers import CHUNK_BYTES, file_chunks

_READ_FILE = """
import sys
remaining = int(sys.argv[2])
with open(sys.argv[1], 'rb') as source:
    while remaining:
        chunk = source.read(min(remaining, 65536))
        if not chunk:
            break
        sys.stdout.buffer.write(chunk)
        remaining -= len(chunk)
"""

_WRITE_FILE = """
import shutil, sys
with open(sys.argv[1], 'wb') as destination:
    shutil.copyfileobj(sys.stdin.buffer, destination, 65536)
"""


class ModalExecution(ModalReference, NativeExecution[ModalEnvironmentConfiguration, ModalState]):
    async def open(self, *, execution_id: str) -> None:
        self.require_target()
        async with asyncio.timeout(self.config.request_timeout_seconds):
            sandbox = await self.lookup()
            if sandbox is None:
                raise failure(self.provider_key, "provider_target_unavailable", Category.UNAVAILABLE)
            self.sandbox = sandbox
            await self.open_operations(self.execute, sandbox.object_id, execution_id)

    async def execute(self, argv: list[str], timeout: float) -> str:
        assert self.sandbox is not None
        return await execute_command(self.sandbox, argv, timeout)

    async def read_stream(self, path: str) -> AsyncIterator[bytes]:
        assert self.sandbox is not None
        with sdk_errors():
            async with asyncio.timeout(self.config.request_timeout_seconds):
                process = await self.sandbox.exec.aio(
                    self.config.python,
                    "-I",
                    "-c",
                    _READ_FILE,
                    path,
                    str(self.config.max_file_bytes + 1),
                    text=False,
                    timeout=int(self.config.request_timeout_seconds) + 1,
                )
                async with aclosing(aiter(process.stdout)) as chunks:
                    async for chunk in chunks:
                        for offset in range(0, len(chunk), CHUNK_BYTES):
                            yield chunk[offset : offset + CHUNK_BYTES]
                if await process.wait.aio() != 0:
                    raise failure(self.provider_key, "provider_command_failed", Category.PROVIDER_FAILURE)

    async def write_stream(self, path: str, source: BinaryIO, *, mode: int) -> None:
        del mode  # Write into the guest-created candidate without replacing its inode.
        assert self.sandbox is not None
        with sdk_errors(mutation=True):
            async with asyncio.timeout(self.config.request_timeout_seconds):
                process = await self.sandbox.exec.aio(
                    self.config.python,
                    "-I",
                    "-c",
                    _WRITE_FILE,
                    path,
                    text=False,
                    timeout=int(self.config.request_timeout_seconds) + 1,
                )
                try:
                    async with closing_stream(file_chunks(source)) as chunks:
                        async for chunk in chunks:
                            process.stdin.write(chunk)
                            await process.stdin.drain.aio()
                finally:
                    process.stdin.write_eof()
                await process.stdin.drain.aio()
                if await process.wait.aio() != 0:
                    raise failure(
                        self.provider_key,
                        "provider_command_failed",
                        Category.PROVIDER_FAILURE,
                        certainty=Certainty.KNOWN,
                    )
