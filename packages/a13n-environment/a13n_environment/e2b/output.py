"""Bounded SDK-text observations, independent of native command lifetime."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from ..commands import ProcessStatus, ProcessStreamRead
from ..models import EnvironmentError
from ..retention import EnvironmentOutputCapture, EnvironmentOutputPolicy, EnvironmentOutputSegment

if TYPE_CHECKING:
    from e2b.sandbox_async.commands.command_handle import AsyncCommandHandle


@dataclass(slots=True)
class ObservationPool:
    """Active attachments and retained bytes have independent admission budgets.

    All accounting is synchronous on the adapter's event loop, including callbacks
    received before the SDK publishes a command handle. Only closed logs are evicted.
    """

    max_active: int
    max_bytes: int
    active: int = 0
    retained_bytes: int = 0
    closed: OrderedDict[int, CommandObservation] = field(default_factory=OrderedDict)

    def reserve(self, observation: CommandObservation) -> None:
        if self.active >= self.max_active:
            raise EnvironmentError("E2B active observation capacity is exhausted.", code="environment_limit_exceeded")
        self.closed.pop(id(observation), None)
        self.active += 1
        observation.active = True
        observation.closed = False
        observation.detached.clear()

    def finish(self, observation: CommandObservation) -> None:
        if observation.active:
            self.active -= 1
            observation.active = False
        observation.closed = True
        if observation.retained_bytes:
            self.closed.setdefault(id(observation), observation)

    def claim(self, size: int) -> int:
        while self.retained_bytes + size > self.max_bytes and self.closed:
            _, oldest = self.closed.popitem(last=False)
            self.retained_bytes -= oldest.retained_bytes
            oldest.stdout.clear()
            oldest.stderr.clear()
            oldest.reason = "observation_evicted"
            oldest.changed.set()
        accepted = min(size, self.max_bytes - self.retained_bytes)
        self.retained_bytes += accepted
        return accepted

    def release(self, observation: CommandObservation) -> None:
        self.finish(observation)
        self.closed.pop(id(observation), None)
        self.retained_bytes -= observation.retained_bytes
        observation.stdout.clear()
        observation.stderr.clear()


@dataclass(slots=True)
class CommandObservation:
    """One cumulative Run-local log, retained across transient native attachments."""

    limit: int
    pool: ObservationPool
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    stdout_end: int = 0
    stderr_end: int = 0
    native: AsyncCommandHandle | None = None
    consumer: asyncio.Task[None] | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    capped: asyncio.Event = field(default_factory=asyncio.Event)
    detached: asyncio.Event = field(default_factory=asyncio.Event)
    active: bool = False
    closed: bool = True
    reason: Literal["reattached", "connection_lost", "observation_limit", "observation_evicted"] | None = None
    terminal: ProcessStatus | None = None

    @property
    def retained_bytes(self) -> int:
        return len(self.stdout) + len(self.stderr)

    async def append(self, stream: Literal["stdout", "stderr"], text: str) -> None:
        remaining = self.limit - self.stdout_end - self.stderr_end
        data = text.encode("utf-8")
        accepted = self.pool.claim(min(len(data), remaining))
        target = self.stdout if stream == "stdout" else self.stderr
        target.extend(data[:accepted])
        if stream == "stdout":
            self.stdout_end += accepted
        else:
            self.stderr_end += accepted
        if len(data) >= remaining or accepted < len(data):
            self.reason = "observation_limit"
            self.capped.set()
        self.changed.set()
        if self.capped.is_set():
            # Backpressure stops already-ready SDK events before the separate
            # owner disconnects. Never disconnect from inside the SDK callback.
            await self.detached.wait()

    def attach(self, native: AsyncCommandHandle) -> None:
        self.native = native
        self.detached.clear()
        self.consumer = asyncio.create_task(self._consume(native), name="e2b-command-observation")

    async def _consume(self, native: AsyncCommandHandle) -> None:
        from e2b.sandbox.commands.command_handle import CommandExitException

        waiting = asyncio.create_task(native.wait())
        limiting = asyncio.create_task(self.capped.wait())
        try:
            await asyncio.wait((waiting, limiting), return_when=asyncio.FIRST_COMPLETED)
            if waiting.done() and not waiting.cancelled():
                try:
                    result = waiting.result()
                    exit_code = result.exit_code
                except CommandExitException as error:
                    exit_code = error.exit_code
                self.terminal = ProcessStatus(phase="exited", termination_reason="exit", exit_code=exit_code)
                self.pool.finish(self)
                self.changed.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self.capped.is_set():
                self.reason = "connection_lost"
        finally:
            # One owner releases the SDK stream and its duplicated text even at
            # terminal completion. Closed history retains no SDK handle or waiter.
            try:
                await native.disconnect()
            finally:
                waiting.cancel()
                limiting.cancel()
                await asyncio.gather(waiting, limiting, return_exceptions=True)
                self.native = None
                self.consumer = None
                self.detached.set()
                self.pool.finish(self)
                self.changed.set()

    async def disconnect(self) -> None:
        consumer = self.consumer
        if consumer is not None:
            if self.native is not None:
                await self.native.disconnect()
            await asyncio.shield(consumer)
        self.detached.set()
        self.pool.finish(self)
        self.changed.set()

    def read(
        self, stream: Literal["stdout", "stderr"], offset: int, policy: EnvironmentOutputPolicy
    ) -> ProcessStreamRead:
        buffer = self.stdout if stream == "stdout" else self.stderr
        end = self.stdout_end if stream == "stdout" else self.stderr_end
        start = end - len(buffer)
        if offset < 0 or offset > end:
            raise EnvironmentError("Invalid observation offset.", code="environment_cursor_invalid")
        offset = max(offset, start)
        data = bytes(buffer[offset - start : offset - start + min(policy.max_inline_bytes, policy.max_output_bytes)])
        capture = EnvironmentOutputCapture(
            kind="inline" if data else "empty",
            origin="sdk_text",
            coverage="partial" if self.reason else "complete",
            observation_closed=self.closed,
            reason=self.reason,
            producer_complete=None,
            content_complete=self.terminal is not None and self.reason is None,
            produced_bytes=None,
            captured_bytes=len(buffer),
            dropped_bytes=None,
            inline=data,
            available_start=start,
            available_end=end,
        )
        return ProcessStreamRead(
            chunks=(EnvironmentOutputSegment(start_offset=offset, data=data),) if data else (),
            next_cursor=None,
            capture=capture,
        )

    def materialize(
        self, stream: Literal["stdout", "stderr"], policy: EnvironmentOutputPolicy
    ) -> EnvironmentOutputCapture:
        capture = self.read(stream, 0, policy.model_copy(update={"max_inline_bytes": policy.max_output_bytes})).capture
        data = capture.inline or b""
        omitted = len(data) != capture.captured_bytes
        return capture.model_copy(
            update={
                "captured_bytes": len(data),
                "available_end": capture.available_start + len(data),
                "content_complete": capture.content_complete and not omitted,
                "coverage": "partial" if omitted else capture.coverage,
            }
        )
