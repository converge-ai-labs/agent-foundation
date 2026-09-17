"""Bounded Worker waiters and independent response dispatch for relay operations."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections import OrderedDict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal

from a13n_environment.models import EnvironmentError

from .authority import DispatchAuthority, DispatchDenied, LeaseDeadline, UseIdentity
from .relay_flow import TransferWindow
from .relay_protocol import (
    CONTROL_OPERATIONS,
    RelayChunk,
    RelayCredit,
    RelayFailure,
    RelayFrame,
    RelayRequest,
    RelayTerminal,
    TransferPosition,
)
from .relay_storage import RelayStoreError, WorkerResponseMailbox


class RelayOperationError(EnvironmentError):
    def __init__(self, failure: RelayFailure) -> None:
        self.failure = failure
        super().__init__(
            "Client Environment operation did not complete",
            code=failure.code,
            details=failure.details,
            retry_hint=failure.retry_hint,
        )


@dataclass(frozen=True, slots=True)
class ReceivedChunk:
    position: TransferPosition
    data: bytes = field(repr=False)


class PendingRelayRequest:
    def __init__(
        self,
        request: RelayRequest,
        authority: DispatchAuthority,
        deadline: LeaseDeadline,
        *,
        streaming: Literal["download", "upload"] | None,
        chunk_capacity: int,
        chunk_bytes: int,
    ) -> None:
        self.request = request
        self._authority = authority
        self._deadline = deadline.monotonic_at
        self._streaming = streaming
        self._chunk_capacity = chunk_capacity
        self._chunk_bytes = chunk_bytes
        self._chunks: deque[ReceivedChunk] = deque()
        self._history: OrderedDict[int, tuple[TransferPosition, bytes]] = OrderedDict()
        transfer_id = request.payload.get("transfer_id")
        if streaming == "upload" and not isinstance(transfer_id, str):
            raise ValueError("Upload waiters require a transfer identity")
        self.upload = (
            TransferWindow(transfer_id, chunk_capacity)
            if streaming == "upload" and isinstance(transfer_id, str)
            else None
        )
        self._upload_finished = False
        self._transfer_id: str | None = transfer_id if isinstance(transfer_id, str) else None
        self._sequence = self._offset = 0
        self._terminal: RelayTerminal | None = None
        self._failure: RelayFailure | None = None
        self._changed = asyncio.Event()
        self._possibly_published = False
        self._remote_completed = False

    @property
    def needs_cancellation(self) -> bool:
        return self._possibly_published and not self._remote_completed

    def begin_publication(self) -> None:
        self._check()
        self._possibly_published = True

    def fail(self, code: str, *, certainty: str | None = None) -> None:
        self._accept_failure(
            RelayFailure.model_validate(
                {"code": code, "certainty": certainty or ("unknown" if self._possibly_published else "not_dispatched")}
            )
        )

    def _accept_failure(self, failure: RelayFailure) -> None:
        if self._failure is None and self._terminal is None:
            self._failure = failure
            if self._failure.certainty == "not_dispatched":
                self._possibly_published = False
            self._chunks.clear()
            self._changed.set()

    def accept(self, frame: RelayFrame) -> None:
        if frame.request_id != self.request.request_id or frame.use != self.request.use:
            return
        if self._terminal is not None or self._failure is not None:
            return
        try:
            self._check()
        except RelayOperationError:
            return
        if isinstance(frame, RelayChunk):
            self._accept_chunk(frame)
        elif isinstance(frame, RelayCredit):
            if self.upload is None:
                self.fail("environment_transfer_incomplete")
            else:
                try:
                    self.upload.credit(frame.transfer)
                except EnvironmentError:
                    self.fail("environment_transfer_incomplete")
                self._changed.set()
        elif frame.error is not None:
            self._remote_completed = True
            self._accept_failure(frame.error)
        elif self._valid_terminal(frame):
            self._remote_completed = True
            self._terminal = frame
            self._changed.set()
        else:
            self.fail("environment_transfer_incomplete")

    def _accept_chunk(self, frame: RelayChunk) -> None:
        if self._streaming != "download" or len(frame.data) > ((self._chunk_bytes + 2) // 3) * 4:
            self.fail("environment_transfer_incomplete")
            return
        try:
            data = base64.b64decode(frame.data, validate=True)
        except ValueError:
            self.fail("environment_transfer_incomplete")
            return
        if len(data) > self._chunk_bytes:
            self.fail("environment_transfer_incomplete")
            return
        position = frame.transfer
        digest = hashlib.sha256(data).digest()
        if position.sequence < self._sequence:
            if self._history.get(position.sequence) != (position, digest):
                self.fail("environment_transfer_incomplete")
            return
        if (
            position.sequence != self._sequence
            or position.offset != self._offset
            or (self._transfer_id is not None and position.transfer_id != self._transfer_id)
        ):
            self.fail("environment_transfer_incomplete")
            return
        if len(self._chunks) >= self._chunk_capacity:
            self.fail("environment_overloaded")
            return
        self._transfer_id = position.transfer_id
        self._sequence += 1
        self._offset += len(data)
        self._history[position.sequence] = (position, digest)
        if len(self._history) > 128:
            self._history.popitem(last=False)
        self._chunks.append(ReceivedChunk(position, data))
        self._changed.set()

    def _valid_terminal(self, frame: RelayTerminal) -> bool:
        if not self._streaming:
            return frame.transfer is None
        if self.upload is not None:
            return self._upload_finished and frame.transfer == self.upload.position
        position = frame.transfer
        return (
            position is not None
            and position.sequence == self._sequence
            and position.offset == self._offset
            and (self._transfer_id is None or position.transfer_id == self._transfer_id)
        )

    def finish_upload(self) -> TransferPosition:
        self._check()
        if self.upload is None:
            raise ValueError("Only uploads have an input terminator")
        self._upload_finished = True
        return self.upload.position

    async def upload_slot(self) -> TransferWindow:
        if self.upload is None:
            raise ValueError("Only uploads publish input data")
        self._check()
        # Credit and failure wake the common waiter; no second queue can mask
        # use loss while a producer is waiting for remote capacity.
        while not self.upload.has_capacity:
            await self._wait_change()
            self._check()
        return self.upload

    async def result(self) -> RelayTerminal:
        while True:
            self._check()
            if self._terminal is not None:
                return self._terminal
            await self._wait_change()

    async def next_chunk(self) -> ReceivedChunk | None:
        if self._streaming != "download":
            raise ValueError("Only downloads have a chunk iterator")
        while True:
            self._check()
            if self._chunks:
                return self._chunks.popleft()
            if self._terminal is not None:
                return None
            await self._wait_change()

    def _check(self) -> None:
        if self._terminal is None and self._failure is None:
            if monotonic() >= self._deadline:
                self.fail("environment_timeout")
            else:
                try:
                    self._authority.check(self.request.use)
                except DispatchDenied:
                    self.fail("environment_unavailable")
        if self._failure is not None:
            raise RelayOperationError(self._failure)

    async def _wait_change(self) -> None:
        self._changed.clear()
        try:
            async with asyncio.timeout_at(min(self._deadline, self._authority.deadline)):
                await self._changed.wait()
        except TimeoutError:
            # A renewal may have extended the authority while this wait slept.
            self._check()


class RelayResponseDispatcher:
    def __init__(
        self,
        mailbox: WorkerResponseMailbox,
        *,
        max_pending: int = 128,
        control_reserve: int = 16,
        chunk_capacity: int = 8,
        chunk_bytes: int = 65_536,
    ) -> None:
        if not 0 < control_reserve < max_pending or min(chunk_capacity, chunk_bytes) < 1:
            raise ValueError("Relay waiter capacity must be bounded with a control reserve")
        self._mailbox = mailbox
        self._max_pending = max_pending
        self._control_reserve = control_reserve
        self._chunk_capacity = chunk_capacity
        self._chunk_bytes = chunk_bytes
        self._pending: dict[str, PendingRelayRequest] = {}
        self._closed = False
        self._running = False

    @contextmanager
    def register(
        self,
        request: RelayRequest,
        authority: DispatchAuthority,
        deadline: LeaseDeadline,
        *,
        streaming: Literal["download", "upload"] | None = None,
    ) -> Iterator[PendingRelayRequest]:
        if self._closed or request.use.worker_instance_id != self._mailbox.worker_instance_id:
            raise RelayOperationError(RelayFailure(code="environment_unavailable", certainty="not_dispatched"))
        limit = (
            self._max_pending if request.operation in CONTROL_OPERATIONS else self._max_pending - self._control_reserve
        )
        if len(self._pending) >= limit:
            raise RelayOperationError(RelayFailure(code="environment_overloaded", certainty="not_dispatched"))
        if request.request_id in self._pending:
            raise ValueError("Relay request already has a waiter")
        authority.check(request.use)
        pending = PendingRelayRequest(
            request,
            authority,
            deadline,
            streaming=streaming,
            chunk_capacity=self._chunk_capacity,
            chunk_bytes=self._chunk_bytes,
        )
        self._pending[request.request_id] = pending
        try:
            yield pending
        finally:
            self._pending.pop(request.request_id, None)
            pending.fail("environment_cancelled")

    def accept(self, frame: RelayFrame) -> None:
        pending = self._pending.get(frame.request_id)
        if pending is not None:
            pending.accept(frame)

    def fence_use(self, use: UseIdentity) -> None:
        for pending in self._pending.values():
            if pending.request.use == use:
                pending.fail("environment_unavailable")

    def close(self) -> None:
        self._closed = True
        for pending in self._pending.values():
            pending.fail("environment_unavailable")
        self._pending.clear()

    async def run(self) -> None:
        if self._running:
            raise RuntimeError("Worker relay responses already have a reader")
        self._running = True
        recover = True
        failures = 0
        unacknowledged: tuple[str, ...] = ()
        try:
            while not self._closed:
                try:
                    if unacknowledged:
                        await self._mailbox.acknowledge(*unacknowledged)
                        unacknowledged = ()
                    frames = await self._mailbox.read(pending=recover)
                    if not frames:
                        recover = False
                    for _, frame in frames:
                        self.accept(frame)
                    unacknowledged = tuple(entry_id for entry_id, _ in frames)
                    failures = 0
                except RelayStoreError as error:
                    failures += 1
                    if error.code != "relay_unavailable" or failures > 2:
                        raise
                    recover = True
                    await asyncio.sleep(0.05)
        finally:
            self.close()
