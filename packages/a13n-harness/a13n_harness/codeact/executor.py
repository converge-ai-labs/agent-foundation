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

type DispatchFn = Callable[[str, dict[str, Any], int, int], Coroutine[Any, Any, Any]]
type PrepareArgsFn = Callable[[dict[str, Any], int], Coroutine[Any, Any, int]]
type AdmitFn = Callable[[str], Coroutine[Any, Any, int]]
type ReleaseFn = Callable[[int], Coroutine[Any, Any, None]]
type AsyncMontyState = AsyncFunctionSnapshot | AsyncFutureSnapshot | AsyncNameLookupSnapshot | MontyComplete
type PendingCall = tuple[asyncio.Task[Any], int | None]


def is_sandbox_panic(exc: BaseException) -> bool:
    """Return whether a Rust-side pyo3 panic escaped the sandbox binding."""

    return type(exc).__name__ == "PanicException"


@dataclass
class MontyExecutor:
    """Single-use host loop for one Monty feed."""

    dispatch: DispatchFn
    admit: AdmitFn
    prepare_args: PrepareArgsFn
    release: ReleaseFn
    valid_names: Container[str]
    sequential_names: set[str] = field(default_factory=set)
    global_sequential: bool = False
    max_concurrency: int = 1
    _pending: dict[int, PendingCall] = field(default_factory=dict, init=False)
    _active_ordinals: set[int] = field(default_factory=set, init=False)
    _admission: asyncio.Semaphore = field(init=False, repr=False)
    _sequential_tail: asyncio.Task[Any] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_concurrency <= 0:
            raise ValueError("max_concurrency must be greater than zero")
        self._admission = asyncio.Semaphore(self.max_concurrency)

    async def _release_once(self, ordinal: int) -> None:
        if ordinal in self._active_ordinals:
            self._active_ordinals.remove(ordinal)
            await self.release(ordinal)

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
        calls = tuple(self._pending.values())
        self._pending.clear()
        tasks = [task for task, _ in calls]
        for task in tasks:
            task.cancel()
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
        for ordinal in list(self._active_ordinals):
            await self._release_once(ordinal)
        if interrupted:
            raise asyncio.CancelledError

    async def _handle_function(self, snapshot: AsyncFunctionSnapshot) -> AsyncMontyState:
        if snapshot.is_os_function:
            if str(snapshot.function_name) == "system.async_sleep":
                # Timers and host calls must settle on the same snapshot driver.
                (delay,) = snapshot.args
                self._pending[snapshot.call_id] = (asyncio.create_task(asyncio.sleep(delay)), None)
                return await snapshot.resume({"future": ...})
            return await snapshot.resume_auto()

        name = str(snapshot.function_name)
        if name not in self.valid_names:
            return await snapshot.resume({"exception": NameError(f"Unknown function: {name}")})

        # Charge arguments before queueing so waiting calls cannot retain unbudgeted data.
        ordinal = await self.admit(name)
        self._active_ordinals.add(ordinal)
        try:
            if snapshot.args:
                raise TypeError(f"{name}() does not accept positional arguments; use keyword arguments")
            kwargs = snapshot.kwargs
            argument_bytes = await self.prepare_args(kwargs, ordinal)
        except Exception as exc:
            await self._release_once(ordinal)
            return await snapshot.resume({"exception": exc})
        except BaseException:
            await self._release_once(ordinal)
            raise

        barrier = name in self.sequential_names
        if barrier:
            predecessors = [task for task, admitted in self._pending.values() if admitted is not None]
        else:
            predecessors = [self._sequential_tail] if self._sequential_tail is not None else []
        task = asyncio.create_task(self._dispatch_admitted(name, kwargs, ordinal, argument_bytes, predecessors))
        self._pending[snapshot.call_id] = (task, ordinal)
        if barrier or self.global_sequential:
            self._sequential_tail = task
        return await snapshot.resume({"future": ...})

    async def _dispatch_admitted(
        self,
        name: str,
        kwargs: dict[str, Any],
        ordinal: int,
        argument_bytes: int,
        predecessors: list[asyncio.Task[Any]],
    ) -> Any:
        try:
            if predecessors:
                await asyncio.shield(asyncio.gather(*predecessors, return_exceptions=True))
            async with self._admission:
                return await self.dispatch(name, kwargs, ordinal, argument_bytes)
        finally:
            await self._release_once(ordinal)

    async def _resolve_futures(self, snapshot: AsyncFutureSnapshot) -> AsyncMontyState:
        pending = {call_id: self._pending[call_id] for call_id in snapshot.pending_call_ids}
        await asyncio.wait([task for task, _ in pending.values()], return_when=asyncio.FIRST_COMPLETED)
        results: dict[int, ExternalSettledResult] = {}
        for call_id, (task, _) in pending.items():
            if task.done():
                results[call_id] = await _await_external(task)
                del self._pending[call_id]
        return await snapshot.resume(results)


async def _await_external(call: asyncio.Task[Any]) -> ExternalReturnValue | ExternalException:
    try:
        result = await call
    except Exception as exc:
        return ExternalException(exception=exc)
    return ExternalReturnValue(return_value=result)
