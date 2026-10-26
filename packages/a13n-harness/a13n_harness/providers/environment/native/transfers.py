"""Bounded binary request bodies for native filesystem APIs."""

import asyncio
import secrets
import tarfile
import zlib
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import BinaryIO

from a13n_harness.environment._streams import closing_stream
from a13n_harness.environment.models import EnvironmentError

CHUNK_BYTES = 65536
SPRITES_UPLOAD_BYTES = 16 * 1024 * 1024


def remaining_bytes(source: BinaryIO) -> int:
    start = source.tell()
    end = source.seek(0, 2)
    source.seek(start)
    return end - start


async def file_chunks(source: BinaryIO) -> AsyncIterator[bytes]:
    while True:
        pending = asyncio.create_task(asyncio.to_thread(source.read, CHUNK_BYTES))
        try:
            chunk = await asyncio.shield(pending)
        except BaseException:
            # The spool belongs to GuestFiles. Join its bounded read before
            # cancellation lets that owner close/reuse the file descriptor.
            await asyncio.gather(pending, return_exceptions=True)
            raise
        if not chunk:
            return
        yield chunk


async def gzip_file(path: str, source: BinaryIO, mode: int) -> AsyncIterator[bytes]:
    # Extract from / so the HTTP cwd stays ASCII; PAX carries the full Unicode path.
    entry = tarfile.TarInfo(PurePosixPath(path).relative_to("/").as_posix())
    entry.size = remaining_bytes(source)
    entry.mode = mode & 0o777
    compressor = zlib.compressobj(wbits=31)
    yield compressor.compress(entry.tobuf(format=tarfile.PAX_FORMAT))
    total = 0
    async with closing_stream(file_chunks(source)) as chunks:
        async for chunk in chunks:
            total += len(chunk)
            if total > entry.size:
                raise EnvironmentError("Upload source changed.", code="environment_provider_failure")
            if compressed := compressor.compress(chunk):
                yield compressed
    if total != entry.size:
        raise EnvironmentError("Upload source changed.", code="environment_provider_failure")
    yield compressor.compress(bytes((-entry.size % 512) + 1024)) + compressor.flush()


def multipart_file(path: str, source: BinaryIO) -> tuple[str, AsyncIterator[bytes]]:
    boundary = "a13n-" + secrets.token_hex(24)

    async def body() -> AsyncIterator[bytes]:
        yield (
            f'--{boundary}\r\nContent-Disposition: form-data; name="path"\r\n\r\n{path}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="upload"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        async with closing_stream(file_chunks(source)) as chunks:
            async for chunk in chunks:
                yield chunk
        yield f"\r\n--{boundary}--\r\n".encode()

    return f"multipart/form-data; boundary={boundary}", body()
