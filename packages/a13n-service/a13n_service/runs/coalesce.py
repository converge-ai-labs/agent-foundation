"""Bound the latency of producer-local typed batches before sequence allocation."""

import asyncio

from a13n_stream_protocol import DisplayProjector


class Coalescer:
    def __init__(self, projector: DisplayProjector, *, window: float) -> None:
        self.projector, self.window = projector, window
        self.timer: asyncio.TimerHandle | None = None

    async def __aenter__(self) -> "Coalescer":
        if self.window > 0:
            self.timer = asyncio.get_running_loop().call_later(self.window, self._tick)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.timer is not None:
            self.timer.cancel()
        self.projector.flush()

    def _tick(self) -> None:
        self.projector.flush()
        self.timer = asyncio.get_running_loop().call_later(self.window, self._tick)
