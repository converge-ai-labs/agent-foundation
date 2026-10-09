"""Provider-local retained-output store with actual-byte spool accounting."""

from __future__ import annotations

import asyncio
import itertools
import shutil
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from typing import BinaryIO

from .models import EnvironmentError, EnvironmentOperationReceipt
from .retention import (
    BoundOutputCursor,
    BoundOutputReference,
    EnvironmentOutputCapture,
    EnvironmentOutputPolicy,
    EnvironmentOutputReadResult,
    EnvironmentOutputSegment,
    OpaqueOutputCursor,
    OpaqueOutputReference,
    _unwrap_opaque,
)


@dataclass(slots=True)
class _Record:
    path: Path
    size: int
    producer_complete: bool
    content_complete: bool
    produced_bytes: int
    dropped_bytes: int


class LocalRetentionWriter:
    def __init__(
        self,
        store: LocalRetentionStore,
        token: str,
        path: Path,
        max_bytes: int,
        file: BinaryIO,
    ) -> None:
        self._store = store
        self._token = token
        self._path = path
        self._max_bytes = max_bytes
        self._file = file
        self._written = 0
        self._accepting = True
        self._finished = False
        self._write_lock = asyncio.Lock()

    async def write(self, chunk: bytes) -> int:
        """Write as much as both this capture and the aggregate spool can accept."""
        if not isinstance(chunk, bytes):
            raise TypeError("retained-output writer chunks must be bytes")
        async with self._write_lock:
            if self._finished:
                raise EnvironmentError("Retention writer is closed.", code="environment_conflict")
            if not self._accepting:
                return 0
            requested = min(len(chunk), self._max_bytes - self._written)
            accepted = await self._store._claim(requested)
            if accepted < len(chunk):
                self._accepting = False
            if accepted == 0:
                return 0
            write_task = asyncio.create_task(asyncio.to_thread(self._file.write, chunk[:accepted]))
            try:
                written = await asyncio.shield(write_task)
            except asyncio.CancelledError:
                try:
                    written = await asyncio.shield(write_task)
                except BaseException:
                    await self._store._refund(accepted)
                else:
                    await self._account_write(accepted, written)
                raise
            except BaseException:
                await self._store._refund(accepted)
                raise
            await self._account_write(accepted, written)
            if written != accepted:
                raise EnvironmentError("Retained-output write was incomplete.", code="environment_provider_failure")
            return written

    async def _account_write(self, claimed: int, written: int) -> None:
        if written < 0 or written > claimed:
            await self._store._refund(claimed)
            raise EnvironmentError(
                "Retained-output write returned an invalid size.", code="environment_provider_failure"
            )
        self._written += written
        if written < claimed:
            await self._store._refund(claimed - written)

    async def commit(
        self,
        *,
        producer_complete: bool = True,
        content_complete: bool = True,
        produced_bytes: int | None = None,
        dropped_bytes: int = 0,
    ) -> BoundOutputReference:
        async with self._write_lock:
            if self._finished:
                raise EnvironmentError("Retention writer is closed.", code="environment_conflict")
            effective_produced = self._written if produced_bytes is None else produced_bytes
            if effective_produced < self._written or dropped_bytes != effective_produced - self._written:
                raise EnvironmentError("Retained-output provenance is invalid.", code="environment_provider_failure")
            await asyncio.to_thread(self._file.flush)
            await asyncio.to_thread(self._file.close)
            reference = await self._store._commit(
                self._token,
                self._path,
                self._written,
                producer_complete=producer_complete,
                content_complete=content_complete,
                produced_bytes=effective_produced,
                dropped_bytes=dropped_bytes,
            )
            self._finished = True
            return reference

    async def abort(self) -> None:
        async with self._write_lock:
            if self._finished:
                return
            try:
                await asyncio.to_thread(self._file.close)
            finally:
                self._finished = True
                await self._store._abort(self._path, self._written)


class LocalRetentionStore:
    def __init__(
        self,
        *,
        root: Path,
        execution_id: str,
        generation: str,
        max_spool_bytes: int,
    ) -> None:
        self._root = root
        self._execution_id = execution_id
        self._generation = generation
        self._max_spool_bytes = max_spool_bytes
        self._records: dict[str, _Record] = {}
        self._used_bytes = 0
        self._lock = asyncio.Lock()
        self._operations = itertools.count(1)
        self._closed = False

    async def reserve(self, *, max_bytes: int) -> LocalRetentionWriter:
        """Open a candidate without reserving its worst-case byte count."""
        if max_bytes <= 0:
            raise EnvironmentError("Invalid retained-output capture limit.", code="environment_request_invalid")
        async with self._lock:
            if self._closed:
                raise EnvironmentError("Retained-output store is closed.", code="environment_closed")
            token = token_hex(16)
            path = self._root / f"candidate-{token}"
            file = await asyncio.to_thread(path.open, "xb")
        return LocalRetentionWriter(self, token, path, max_bytes, file)

    async def _claim(self, requested: int) -> int:
        if requested <= 0:
            return 0
        async with self._lock:
            if self._closed:
                raise EnvironmentError("Retained-output store is closed.", code="environment_closed")
            accepted = min(requested, max(self._max_spool_bytes - self._used_bytes, 0))
            self._used_bytes += accepted
            return accepted

    async def _refund(self, size: int) -> None:
        if size <= 0:
            return
        async with self._lock:
            self._used_bytes -= size
            if self._used_bytes < 0:
                self._used_bytes = 0
                raise EnvironmentError("Retained-output accounting underflowed.", code="environment_provider_failure")

    async def _commit(
        self,
        token: str,
        path: Path,
        written: int,
        *,
        producer_complete: bool,
        content_complete: bool,
        produced_bytes: int,
        dropped_bytes: int,
    ) -> BoundOutputReference:
        final = self._root / f"output-{token}"
        async with self._lock:
            if self._closed:
                raise EnvironmentError("Retained-output store is closed.", code="environment_closed")
            await asyncio.to_thread(path.replace, final)
            self._records[token] = _Record(
                path=final,
                size=written,
                producer_complete=producer_complete,
                content_complete=content_complete,
                produced_bytes=produced_bytes,
                dropped_bytes=dropped_bytes,
            )
        return BoundOutputReference(
            execution_id=self._execution_id,
            observed_generation=self._generation,
            reference=OpaqueOutputReference._from_payload(token),
        )

    async def _abort(self, path: Path, written: int) -> None:
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        finally:
            await self._refund(written)

    def _validate_reference(self, reference: BoundOutputReference) -> str:
        if reference.execution_id != self._execution_id or reference.observed_generation != self._generation:
            raise EnvironmentError("Retained-output reference is foreign or stale.", code="environment_stale_mount")
        return _unwrap_opaque(reference.reference, OpaqueOutputReference)

    async def read(
        self,
        reference: BoundOutputReference,
        *,
        cursor: BoundOutputCursor | None = None,
        start_offset: int | None = None,
        policy: EnvironmentOutputPolicy,
    ) -> EnvironmentOutputReadResult:
        if cursor is not None and start_offset is not None:
            raise EnvironmentError("Specify cursor or start_offset, not both.", code="environment_request_invalid")
        token = self._validate_reference(reference)
        if cursor is not None:
            if cursor.execution_id != self._execution_id or cursor.observed_generation != self._generation:
                raise EnvironmentError("Output cursor is foreign or stale.", code="environment_stale_mount")
            raw = _unwrap_opaque(cursor.cursor, OpaqueOutputCursor)
            try:
                cursor_token, raw_offset = raw.split(":", 1)
                offset = int(raw_offset)
            except ValueError:
                raise EnvironmentError("Output cursor is invalid.", code="environment_cursor_invalid") from None
            if cursor_token != token:
                raise EnvironmentError("Output cursor does not match reference.", code="environment_cursor_invalid")
        else:
            offset = start_offset or 0
        if offset < 0:
            raise EnvironmentError("Output offset is invalid.", code="environment_request_invalid")
        async with self._lock:
            record = self._records.get(token)
            if record is None:
                raise EnvironmentError("Retained output is unavailable.", code="environment_not_found")
            size = record.size
            read_size = min(policy.max_inline_bytes, max(size - offset, 0))
            try:
                data = await asyncio.to_thread(_read_range, record.path, offset, read_size)
            except FileNotFoundError:
                raise EnvironmentError("Retained output is unavailable.", code="environment_not_found") from None
        end = offset + len(data)
        complete = end >= size
        next_cursor = None
        if not complete:
            next_cursor = BoundOutputCursor(
                execution_id=self._execution_id,
                observed_generation=self._generation,
                cursor=OpaqueOutputCursor._from_payload(f"{token}:{end}"),
            )
        capture = EnvironmentOutputCapture(
            kind="retained",
            producer_complete=record.producer_complete,
            content_complete=record.content_complete,
            produced_bytes=record.produced_bytes,
            captured_bytes=size,
            dropped_bytes=record.dropped_bytes,
            inline=None,
            preview=(EnvironmentOutputSegment(start_offset=offset, data=data),) if data else (),
            reference=reference,
            cursor=next_cursor,
            available_start=0,
            available_end=size,
            expires_at=None,
        )
        return EnvironmentOutputReadResult(
            chunks=(EnvironmentOutputSegment(start_offset=offset, data=data),) if data else (),
            next_cursor=next_cursor,
            capture=capture,
        )

    async def release(
        self,
        *,
        reference: BoundOutputReference | None = None,
        cursor: BoundOutputCursor | None = None,
    ) -> EnvironmentOperationReceipt:
        if (reference is None) == (cursor is None):
            raise EnvironmentError(
                "Release requires exactly one reference or cursor.", code="environment_request_invalid"
            )
        if reference is not None:
            token = self._validate_reference(reference)
        else:
            assert cursor is not None
            if cursor.execution_id != self._execution_id or cursor.observed_generation != self._generation:
                raise EnvironmentError("Output cursor is foreign or stale.", code="environment_stale_mount")
            raw = _unwrap_opaque(cursor.cursor, OpaqueOutputCursor)
            token, separator, raw_offset = raw.partition(":")
            if not separator or not raw_offset.isdigit():
                raise EnvironmentError("Output cursor is invalid.", code="environment_cursor_invalid")
        async with self._lock:
            record = self._records.get(token)
            if record is None:
                raise EnvironmentError("Retained output is unavailable.", code="environment_not_found")
            await asyncio.to_thread(record.path.unlink, missing_ok=True)
            self._records.pop(token)
            self._used_bytes -= record.size
        return self._receipt()

    def _receipt(self) -> EnvironmentOperationReceipt:
        return EnvironmentOperationReceipt(
            execution_id=self._execution_id,
            observed_generation=self._generation,
            operation_id=f"operation-{next(self._operations)}",
            stage="completed",
            outcome="succeeded",
        )

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            self._records.clear()
            self._used_bytes = 0
        await asyncio.to_thread(shutil.rmtree, self._root, True)


def _read_range(path: Path, offset: int, size: int) -> bytes:
    with path.open("rb") as file:
        file.seek(offset)
        return file.read(size)


async def create_retention_root(*, prefix: str) -> Path:
    """Own a temporary directory even when cancellation races thread completion."""
    import shutil
    import tempfile

    allocation = asyncio.create_task(asyncio.to_thread(tempfile.mkdtemp, prefix=prefix))
    try:
        return Path(await asyncio.shield(allocation))
    except asyncio.CancelledError as cancellation:
        while True:
            try:
                allocated = await asyncio.shield(allocation)
                break
            except asyncio.CancelledError:
                continue
            except Exception:
                raise cancellation from None
        cleanup = asyncio.create_task(asyncio.to_thread(shutil.rmtree, allocated, True))
        while True:
            try:
                await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError:
                continue
        raise cancellation
