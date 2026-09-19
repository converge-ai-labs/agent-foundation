"""Fixed profile-1 chunk credit; offsets are transport progress, not receipts."""

from __future__ import annotations

import asyncio
from collections import deque

from .eip.v1 import EIP_TRANSFER_WINDOW_CHUNKS
from .errors import EIPProtocolError


class TransferWindow:
    def __init__(self) -> None:
        self.offset = 0
        self._sent: deque[int] = deque()
        self._changed = asyncio.Event()

    @property
    def pending(self) -> bool:
        return bool(self._sent)

    @property
    def full(self) -> bool:
        return len(self._sent) >= EIP_TRANSFER_WINDOW_CHUNKS

    def sent(self, size: int) -> int:
        if self.full or size <= 0:
            raise EIPProtocolError("transfer exceeded its chunk credit")
        offset = self.offset
        self.offset += size
        self._sent.append(self.offset)
        return offset

    def credit(self, offset: int) -> None:
        if offset not in self._sent:
            raise EIPProtocolError("transfer credit is not an outstanding chunk boundary")
        while self._sent.popleft() != offset:
            pass
        self._changed.set()

    async def wait(self, *, drained: bool = False) -> None:
        while self.pending if drained else self.full:
            self._changed.clear()
            await self._changed.wait()
