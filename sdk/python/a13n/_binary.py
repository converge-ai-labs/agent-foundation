"""Adapt caller-owned binary files to the async transport without buffering them."""

import asyncio
from collections.abc import AsyncIterator
from typing import BinaryIO


async def file_chunks(source: BinaryIO) -> AsyncIterator[bytes]:
    while chunk := await asyncio.to_thread(source.read, 64 * 1024):
        yield chunk
