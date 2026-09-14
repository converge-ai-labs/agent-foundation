"""Drive Monty's async snapshot API and dispatch external tool calls.

Portions adapted from pydantic-ai-harness 0.14.0,
Copyright (c) 2026 Pydantic Services Inc., used under the MIT License.
See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Container, Coroutine
from dataclasses import dataclass, field
from typing import Any

from pydantic_monty import (
    AsyncFunctionSnapshot,
    AsyncFutureSnapshot,
    AsyncNameLookupSnapshot,
    ExternalException,
    ExternalReturnValue,
    ExternalSettledResult,
    MontyComplete,
)

type DispatchFn = Callable[[str, dict[str, Any], int], Coroutine[Any, Any, Any]]
type AdmitFn = Callable[[str], Coroutine[Any, Any, int]]
type ReleaseFn = Callable[[int], Coroutine[Any, Any, None]]
type AsyncMontyState = AsyncFunctionSnapshot | AsyncFutureSnapshot | AsyncNameLookupSnapshot | MontyComplete
type PendingCall = asyncio.Task[Any] | Coroutine[Any, Any, Any]


def is_sandbox_panic(exc: BaseException) -> bool:
    """Return whether a Rust-side pyo3 panic escaped the sandbox binding."""

    return type(exc).__name__ == "PanicException"


@dataclass
class MontyExecutor:
    """Single-use host loop for one Monty feed."""

    dispatch: DispatchFn
    admit: AdmitFn
    release: ReleaseFn
    valid_names: Container[str]
    sequential_names: set[str] = field(default_factory=set)
    global_sequential: bool = False
    max_concurrency: int = 1
    _pending: dict[int, PendingCall] = field(default_factory=dict, init=False)
    _admission: asyncio.Semaphore = field(init=False, repr=False)
    _global_tail: asyncio.Task[Any] | None = field(default=None, init=False, repr=False)
    _pre_resolved: dict[int, ExternalSettledResult] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than zero")
        self._admission = asyncio.Semaphore(self.max_concurrency)

    async def run(self, state: AsyncMontyState) -> MontyComplete:
        """Resume snapshots until the feed completes and drain owned calls."""

        try:
            while not isinstance(state, MontyComplete):
                if isinstance(state, AsyncNameLookupSnapshot):
                    state = await state.resume()
                elif isinstance(state, AsyncFunctionSnapshot):
                    state = await self._handle_function(state)
                else:
                    state = await self._resolve_futures(state)
        finally:
            await self._cancel_pending()
        return state

    async def _cancel_pending(self) -> None:
        tasks: list[asyncio.Task[Any]] = []
        for call in self._pending.values():
            if isinstance(call, asyncio.Task):
                call.cancel()
                tasks.append(call)
            else:
                call.close()
        self._pending.clear()
        if not tasks:
            return

        drain = asyncio.gather(*tasks, return_exceptions=True)
        interrupted = False
        while not drain.done():
            try:
                await asyncio.shield(drain)
            except asyncio.CancelledError:
                interrupted = True
                for task in tasks:
                    if not task.done():
                        task.cancel()
        await drain
        if interrupted:
            raise asyncio.CancelledError

    async def _handle_function(self, snapshot: AsyncFunctionSnapshot) -> AsyncMontyState:
        if snapshot.is_os_function:
            return await snapshot.resume_auto()

        name = str(snapshot.function_name)
        if name not in self.valid_names:
            return await snapshot.resume({"exception": NameError(f"Unknown function: {name}")})

        await self._admission.acquire()
        semaphore_owned = True
        ordinal: int | None = None
        try:
            # Reserve both local and parent limits before Monty materializes
            # arguments into host Python objects.
            ordinal = await self.admit(name)
            if snapshot.args:
                return await snapshot.resume(
                    {"exception": TypeError(f"{name}() does not accept positional arguments; use keyword arguments")}
                )

            kwargs = snapshot.kwargs
            if name in self.sequential_names:
                for call_id in list(self._pending):
                    self._pre_resolved[call_id] = await _await_external(self._pending.pop(call_id))
                call = self._dispatch_admitted(name, kwargs, ordinal)
                ordinal = None
                semaphore_owned = False
                # A barrier still exposes an async host function. Returning its
                # value directly makes `await sequential_tool(...)` await a plain
                # value; retain the settled result behind Monty's future boundary.
                self._pre_resolved[snapshot.call_id] = await _await_external(call)
                return await snapshot.resume({"future": ...})

            if self.global_sequential:
                call = self._dispatch_admitted_after(self._global_tail, name, kwargs, ordinal)
            else:
                call = self._dispatch_admitted(name, kwargs, ordinal)
            try:
                task = asyncio.ensure_future(call)
            except BaseException:
                call.close()
                raise
            ordinal = None
            semaphore_owned = False
            self._pending[snapshot.call_id] = task
            if self.global_sequential:
                self._global_tail = task
            return await snapshot.resume({"future": ...})
        finally:
            if ordinal is not None:
                await self.release(ordinal)
            if semaphore_owned:
                self._admission.release()

    async def _dispatch_admitted(self, name: str, kwargs: dict[str, Any], ordinal: int) -> Any:
        try:
            return await self.dispatch(name, kwargs, ordinal)
        finally:
            await self.release(ordinal)
            self._admission.release()

    async def _dispatch_admitted_after(
        self,
        predecessor: asyncio.Task[Any] | None,
        name: str,
        kwargs: dict[str, Any],
        ordinal: int,
    ) -> Any:
        try:
            if predecessor is not None:
                await asyncio.shield(asyncio.gather(predecessor, return_exceptions=True))
            return await self.dispatch(name, kwargs, ordinal)
        finally:
            await self.release(ordinal)
            self._admission.release()

    async def _resolve_futures(self, snapshot: AsyncFutureSnapshot) -> AsyncMontyState:
        pending_ids = snapshot.pending_call_ids
        results: dict[int, ExternalSettledResult] = {}
        for call_id in pending_ids:
            if call_id in self._pre_resolved:
                results[call_id] = self._pre_resolved.pop(call_id)
            elif self.global_sequential:
                results[call_id] = await _await_external(self._pending.pop(call_id))

        gather_ids = [call_id for call_id in pending_ids if call_id not in results]
        if gather_ids:
            settled = await asyncio.shield(
                asyncio.gather(
                    *(self._pending[call_id] for call_id in gather_ids),
                    return_exceptions=True,
                )
            )
            for call_id, outcome in zip(gather_ids, settled, strict=True):
                del self._pending[call_id]
                results[call_id] = _wrap_gathered(outcome)

        return await snapshot.resume(results)


async def _await_external(call: PendingCall) -> ExternalReturnValue | ExternalException:
    try:
        result = await call
    except Exception as exc:
        return ExternalException(exception=exc)
    return ExternalReturnValue(return_value=result)


def _wrap_gathered(outcome: Any) -> ExternalReturnValue | ExternalException:
    if isinstance(outcome, Exception):
        return ExternalException(exception=outcome)
    if isinstance(outcome, BaseException):
        raise outcome
    return ExternalReturnValue(return_value=outcome)
