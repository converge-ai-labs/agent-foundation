"""Bounded, loss-aware publication of Harness observations to the Run Stream."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from a13n_harness import HarnessEvent, HarnessRunResultEvent, HarnessStreamEvent
from a13n_stream_protocol import HarnessAguiObserver

from .agui import HarnessAguiRunStreamWriter
from .redis import RedisRunStream

logger = logging.getLogger("a13n_service.run_stream.publisher")
_STOP = object()


class RunStreamHarnessProjector:
    """Decouple Harness progress from one ordered, bounded Redis writer."""

    def __init__(
        self,
        stream: RedisRunStream,
        *,
        tenant_id: str,
        run_id: str,
        thread_id: str,
        run_attempt_id: str,
        harness_run_id: str,
        observer: HarnessAguiObserver | None = None,
        max_pending_events: int = 64,
        write_timeout_seconds: float = 5,
        flush_timeout_seconds: float = 15,
    ) -> None:
        if max_pending_events < 1 or write_timeout_seconds <= 0 or flush_timeout_seconds <= 0:
            raise ValueError("Harness live projection bounds must be positive")
        self._stream = stream
        self._tenant_id = tenant_id
        self._run_id = run_id
        self._thread_id = thread_id
        self._run_attempt_id = run_attempt_id
        self._harness_run_id = harness_run_id
        self._writer = HarnessAguiRunStreamWriter(
            stream,
            tenant_id=tenant_id,
            run_id=run_id,
            thread_id=thread_id,
            run_attempt_id=run_attempt_id,
            harness_run_id=harness_run_id,
            observer=observer,
        )
        self._queue: asyncio.Queue[HarnessStreamEvent[Any] | object] = asyncio.Queue(maxsize=max_pending_events)
        self._write_timeout_seconds = write_timeout_seconds
        self._flush_timeout_seconds = flush_timeout_seconds
        self._worker: asyncio.Task[None] | None = None
        self._incomplete = False
        self._incomplete_marked = False
        self._closed = False

    def project(self, event: HarnessEvent | HarnessRunResultEvent[Any]) -> None:
        if event.thread_id != self._thread_id or event.run_id != self._harness_run_id:
            raise ValueError("Harness observation does not match the selected RunAttempt")
        if self._closed:
            raise RuntimeError("Harness live projector is closed")
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name=f"run-stream-{self._run_id}")
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._incomplete = True
            logger.warning(
                "Harness live observation queue overflowed",
                extra={
                    "event": "harness_live_projection_overflow",
                    "run_id": self._run_id,
                    "run_attempt_id": self._run_attempt_id,
                    "harness_run_id": self._harness_run_id,
                },
            )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        worker = self._worker
        try:
            if worker is not None:
                await asyncio.wait_for(self._finish(worker), timeout=self._flush_timeout_seconds)
        except TimeoutError:
            self._incomplete = True
            assert worker is not None
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            logger.error(
                "Harness live observation flush timed out",
                extra={
                    "event": "harness_live_projection_flush_timeout",
                    "run_id": self._run_id,
                    "run_attempt_id": self._run_attempt_id,
                    "harness_run_id": self._harness_run_id,
                },
            )
        except asyncio.CancelledError:
            self._incomplete = True
            assert worker is not None
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            raise
        finally:
            await self._mark_incomplete()
        if not self._incomplete:
            await self._complete_attempt_projection()

    async def _finish(self, worker: asyncio.Task[None]) -> None:
        await self._queue.put(_STOP)
        await worker

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _STOP:
                return
            assert isinstance(item, HarnessEvent | HarnessRunResultEvent)
            try:
                await asyncio.wait_for(self._writer.write(item), timeout=self._write_timeout_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._incomplete = True
                logger.exception(
                    "Harness live observation projection failed",
                    extra={
                        "event": "harness_live_projection_failed",
                        "run_id": self._run_id,
                        "run_attempt_id": self._run_attempt_id,
                        "harness_run_id": self._harness_run_id,
                        "harness_sequence": item.sequence,
                    },
                )
            await self._mark_incomplete()

    async def _mark_incomplete(self) -> None:
        if not self._incomplete or self._incomplete_marked:
            return
        try:
            await asyncio.wait_for(
                self._stream.mark_incomplete(self._tenant_id, self._run_id),
                timeout=self._write_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Run Stream incomplete marker failed",
                extra={
                    "event": "run_stream_incomplete_marker_failed",
                    "run_id": self._run_id,
                    "run_attempt_id": self._run_attempt_id,
                },
            )
        else:
            self._incomplete_marked = True

    async def _complete_attempt_projection(self) -> None:
        try:
            await asyncio.wait_for(
                self._stream.complete_attempt_projection(
                    self._tenant_id,
                    self._run_id,
                    run_attempt_id=self._run_attempt_id,
                    harness_run_id=self._harness_run_id,
                ),
                timeout=self._write_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "RunAttempt live projection completion marker failed",
                extra={
                    "event": "run_attempt_live_projection_marker_failed",
                    "run_id": self._run_id,
                    "run_attempt_id": self._run_attempt_id,
                    "harness_run_id": self._harness_run_id,
                },
            )


__all__ = ["RunStreamHarnessProjector"]
