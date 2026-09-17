"""Cumulative transfer credit with a bounded record of outstanding byte ranges."""

from __future__ import annotations

import asyncio
from collections import OrderedDict

from a13n_environment.models import EnvironmentError

from .relay_protocol import TransferPosition


class TransferWindow:
    def __init__(self, transfer_id: str, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("Transfer window capacity must be positive")
        self.position = TransferPosition(transfer_id=transfer_id, sequence=0, offset=0)
        self._capacity = capacity
        self._outstanding: dict[int, TransferPosition] = {}
        self._credited: OrderedDict[int, TransferPosition] = OrderedDict()
        self._changed = asyncio.Event()

    @property
    def has_capacity(self) -> bool:
        return len(self._outstanding) < self._capacity

    async def wait_slot(self) -> None:
        while len(self._outstanding) >= self._capacity:
            self._changed.clear()
            await self._changed.wait()

    def sent(self, size: int) -> TransferPosition:
        if size <= 0 or len(self._outstanding) >= self._capacity:
            raise ValueError("Transfer publication requires data and an available credit")
        start = self.position
        self.position = TransferPosition(
            transfer_id=start.transfer_id, sequence=start.sequence + 1, offset=start.offset + size
        )
        self._outstanding[self.position.sequence] = self.position
        return start

    def credit(self, position: TransferPosition) -> None:
        if self._credited.get(position.sequence) == position:
            return
        if not self._outstanding or next(iter(self._outstanding)) != position.sequence:
            raise EnvironmentError("Transfer credit is out of order", code="environment_transfer_incomplete")
        if self._outstanding[position.sequence] != position:
            raise EnvironmentError("Transfer credit has a foreign byte range", code="environment_transfer_incomplete")
        del self._outstanding[position.sequence]
        self._credited[position.sequence] = position
        if len(self._credited) > 128:
            self._credited.popitem(last=False)
        self._changed.set()
