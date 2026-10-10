"""DirectLocal management implementation."""

from __future__ import annotations

import asyncio
from typing import Literal

from .._backend import ManagementBackend
from .shared import DirectLocalReference, _resolve_shared_root


class DirectLocalManagement(DirectLocalReference, ManagementBackend):
    async def create(self) -> None:
        await asyncio.to_thread(_resolve_shared_root, self._configuration.root.path)

    async def start(self) -> None:
        await self.create()

    async def inspect(self) -> Literal["running", "stopped", "absent"]:
        # These adapters own no durable daemon; their process-local resources
        # end with their owner. Workspace paths are externally retained.
        return "stopped"

    async def stop(self) -> None:
        return None

    async def destroy(self) -> None:
        return None
