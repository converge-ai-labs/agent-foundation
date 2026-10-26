"""App-owned background compaction, independent of foreground Run lifetimes."""

from __future__ import annotations

import asyncio
from contextvars import Context
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

from a13n_harness import HarnessState
from a13n_harness.compaction import (
    CompactionInput,
    CompactionModelRequest,
    CompactionOperation,
    CompactionOperator,
    CompactionResult,
    compaction_disposition,
    generate_compaction,
)
from a13n_logging import get_logger
from filelock import FileLock, Timeout

from a13n_harness_ui.storage.compaction import CompactionRepository

if TYPE_CHECKING:
    from a13n_harness_ui.compaction_model import CompactionBudget

logger = get_logger(__name__)


class BackgroundCompaction:
    """Keep completed work until a selected checkpoint acknowledges it."""

    def __init__(self, repository: CompactionRepository, root: Path) -> None:
        self.budgets: WeakValueDictionary[str, CompactionBudget] = WeakValueDictionary()
        self._repository = repository
        self._root = root / "locks" / "compaction"
        self._operations: dict[str, CompactionOperation] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._generations: dict[str, asyncio.Task[CompactionResult]] = {}
        self._pending: dict[str, CompactionOperation] = {}
        self._owners: dict[str, FileLock] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    def bind(self, request: CompactionModelRequest) -> CompactionOperator:
        return _BoundOperator(self, request)

    def _owner_lock(self, operation_id: str) -> FileLock:
        self._root.mkdir(parents=True, exist_ok=True)
        return FileLock(self._root / f"{sha256(operation_id.encode()).hexdigest()}.lock", thread_local=False)

    async def inspect(self, thread_id: str) -> CompactionOperation | None:
        async with self._lock:
            return await self._inspect(thread_id)

    async def _inspect(self, thread_id: str) -> CompactionOperation | None:
        for pending in tuple(self._pending.values()):
            if pending.input.basis.thread_id == thread_id:
                await self._publish(pending)
        # Current-process execution reads its persisted outcome without database I/O.
        if thread_id in self._operations:
            return self._operations[thread_id]
        operation = await self._repository.get(thread_id)
        if operation is None or operation.status != "running":
            return operation
        lock = self._owner_lock(operation.input.basis.operation_id)
        try:
            lock.acquire(timeout=0)
        except Timeout:
            return operation
        try:
            # A released OS lock proves the owner stopped; never replay generation.
            failed = CompactionOperation(input=operation.input, status="failed", error_code="compaction_interrupted")
            await self._repository.finish(failed)
            return await self._repository.get(thread_id)
        finally:
            lock.release()

    async def submit(self, input: CompactionInput, request: CompactionModelRequest) -> CompactionOperation:
        # Admission must finish registering the App task even if its caller leaves.
        task = asyncio.create_task(self._submit(input, request), context=Context())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise

    async def _submit(self, input: CompactionInput, request: CompactionModelRequest) -> CompactionOperation:
        lock = self._owner_lock(input.basis.operation_id)
        while True:
            async with self._lock:
                if self._closed:
                    raise RuntimeError("Background compaction is closing")
                # Admission rechecks durable authority; another App may have retired a cached result.
                self._operations.pop(input.basis.thread_id, None)
                current = await self._inspect(input.basis.thread_id)
                if current is not None:
                    return current
                try:
                    lock.acquire(timeout=0)
                except Timeout:
                    pass
                else:
                    handed_off = False
                    try:
                        operation = await self._repository.admit(CompactionOperation(input=input, status="running"))
                        if operation.input.basis != input.basis or operation.status != "running":
                            return operation
                        self._operations[input.basis.thread_id] = operation
                        self._owners[input.basis.operation_id] = lock
                        task = asyncio.create_task(self._generate(input, request), context=Context())
                        self._tasks[input.basis.operation_id] = task
                        handed_off = True
                        return operation
                    finally:
                        if not handed_off:
                            lock.release()
            await asyncio.sleep(0.05)

    async def _generate(self, input: CompactionInput, request: CompactionModelRequest) -> None:
        operation_id = input.basis.operation_id
        try:
            try:
                if self._closed:
                    raise asyncio.CancelledError
                generation = asyncio.create_task(generate_compaction(input, request), context=Context())
                self._generations[operation_id] = generation
                result = await generation
                operation = CompactionOperation(input=input, status="ready", result=result)
            except asyncio.CancelledError:
                operation = CompactionOperation(input=input, status="failed", error_code="compaction_interrupted")
            except Exception as exc:
                logger.warning("Background compaction failed", extra={"error_type": type(exc).__name__})
                operation = CompactionOperation(input=input, status="failed", error_code="compaction_failed")
            finally:
                self._generations.pop(operation_id, None)
            # Only generation is cancellable. Publication and its owner lock outlive it.
            async with self._lock:
                self._pending[operation_id] = operation
                await self._publish(operation)
        finally:
            self._tasks.pop(operation_id, None)

    async def _publish(self, operation: CompactionOperation) -> None:
        basis = operation.input.basis
        for attempt in range(3):
            try:
                published = await self._repository.finish(operation)
                if published:
                    self._operations[basis.thread_id] = operation
                else:
                    # Also handles an uncertain commit whose response was lost.
                    current = await self._repository.get(basis.thread_id)
                    if current is not None:
                        self._operations[basis.thread_id] = current
                    else:
                        self._operations.pop(basis.thread_id, None)
                self._pending.pop(basis.operation_id, None)
                lock = self._owners.pop(basis.operation_id, None)
                if lock is not None:
                    lock.release()
                return
            except Exception as exc:
                if attempt == 2:
                    # Retain the generated result privately for the next observation/close.
                    logger.error("Could not persist compaction outcome", extra={"error_type": type(exc).__name__})
                    return
                await asyncio.sleep(0.1 * (attempt + 1))

    async def wait(self, thread_id: str, operation_id: str) -> CompactionOperation:
        while True:
            operation = await self.inspect(thread_id)
            if operation is None or operation.input.basis.operation_id != operation_id:
                raise RuntimeError("Compaction operation was retired")
            if operation.status != "running":
                return operation
            if operation_id in self._pending:
                # inspect already retried publication. Surface the storage failure
                # instead of waiting forever or generating the same summary again.
                raise RuntimeError("Compaction outcome could not be persisted")
            task = self._tasks.get(operation_id)
            if task is not None:
                await asyncio.shield(task)
            else:
                await asyncio.sleep(0.1)

    async def selected(self, state: HarnessState) -> None:
        """Call only with a successfully selected checkpoint, including admission recovery."""
        async with self._lock:
            operation = await self._repository.get(state.thread_id)
            if operation is not None and await compaction_disposition(state, basis=operation.input.basis) is not None:
                await self._repository.retire(state.thread_id, operation.input.basis.operation_id)
                self._operations.pop(state.thread_id, None)

    async def acknowledge(self, state: HarnessState) -> None:
        """A lost acknowledgement never invalidates a committed checkpoint."""
        try:
            await self.selected(state)
        except Exception as exc:
            logger.warning("Could not acknowledge compaction", extra={"error_type": type(exc).__name__})

    async def restore(self, state: HarnessState) -> CompactionResult | None:
        await self.selected(state)
        async with self._lock:
            self._operations.pop(state.thread_id, None)
            operation = await self._inspect(state.thread_id)
        return operation.result if operation is not None else None

    async def stop_admission(self) -> None:
        async with self._lock:
            self._closed = True

    async def close(self) -> None:
        task = asyncio.create_task(self._close(), context=Context())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise

    async def _close(self) -> None:
        async with self._lock:
            self._closed = True
            tasks = tuple(self._tasks.values())
            for generation in self._generations.values():
                generation.cancel()
        # Workers that have not started observe _closed and still publish a terminal state.
        await asyncio.shield(asyncio.gather(*tasks))
        async with self._lock:
            for operation in tuple(self._pending.values()):
                await self._publish(operation)
            for lock in self._owners.values():
                lock.release()
            self._owners.clear()
            self._operations.clear()


class _BoundOperator(CompactionOperator):
    def __init__(self, owner: BackgroundCompaction, request: CompactionModelRequest) -> None:
        self._owner = owner
        self._request = request

    async def submit(self, input: CompactionInput) -> CompactionOperation:
        return await self._owner.submit(input, self._request)

    async def inspect(self, thread_id: str) -> CompactionOperation | None:
        return await self._owner.inspect(thread_id)

    async def wait(self, thread_id: str, operation_id: str) -> CompactionOperation:
        return await self._owner.wait(thread_id, operation_id)
