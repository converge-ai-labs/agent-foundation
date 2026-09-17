"""Bounded Control execution with evidence preceding every possible effect."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Protocol

from a13n_environment.models import EnvironmentError
from pydantic import JsonValue

from a13n_service.ids import ObjectId

from ..domain import DomainModel
from .authority import DispatchAuthority, DispatchDenied, UseIdentity
from .coordination import ConfirmedObservation
from .relay_protocol import CONTROL_OPERATIONS, RelayFailure, RelayRequest, RelayTerminal
from .relay_storage import ConnectionRelayStore, RelayStoreError


class RelayOperationDispatch(Protocol):
    def prepare(self, operation: str, payload: dict[str, JsonValue]) -> Callable[[], Awaitable[JsonValue]]: ...


class CancelRequest(DomainModel):
    request_id: ObjectId


@dataclass(frozen=True, slots=True)
class _Executing:
    request: RelayRequest
    task: asyncio.Task[None]


class RelayControlConsumer:
    """One online connection's exclusively admitted use, never a global reader.

    The owning Session must bind the same authority to its raw write gate. This
    consumer checks admission locally; it never holds that gate while waiting for
    operation results. Its caller owns connection renewal and carrier teardown.
    """

    def __init__(
        self,
        store: ConnectionRelayStore,
        authority: DispatchAuthority,
        observation: ConfirmedObservation,
        dispatch: RelayOperationDispatch,
        *,
        concurrency: int = 32,
        control_concurrency: int = 4,
    ) -> None:
        use = authority.identity
        if (
            not isinstance(use, UseIdentity)
            or use.connection != store.connection
            or observation.value.status != "online"
            or observation.value.connection != use.connection
            or observation.value.use is None
            or observation.value.use.identity != use
        ):
            raise ValueError("Control consumer requires an online, exact admitted use")
        if not 1 <= concurrency <= 128 or not 1 <= control_concurrency <= 16:
            raise ValueError("Control relay concurrency is outside its bounded range")
        self._store = store
        self._authority = authority
        self._use = use
        self._dispatch = dispatch
        self._observation = observation
        self._concurrency = concurrency
        self._control_concurrency = control_concurrency
        self._executing: dict[str, _Executing] = {}
        self._closed = False
        self._running = False

    async def run(self) -> None:
        if self._running or self._closed:
            raise RuntimeError("Control relay consumer can run only once")
        self._running = True
        pending, failures = True, 0
        after_id = "0-0"
        try:
            async with asyncio.TaskGroup() as tasks:
                try:
                    while not self._closed:
                        self._authority.check(self._use)
                        try:
                            rows = await self._store.read(pending=pending, after_id=after_id)
                        except RelayStoreError:
                            failures += 1
                            if failures >= 2:
                                raise
                            pending = True
                            after_id = "0-0"
                            continue
                        failures = 0
                        if pending and rows:
                            after_id = rows[-1][0]
                        else:
                            pending, after_id = False, "0-0"
                        for entry, request in rows:
                            self._authority.check(self._use)
                            if self._closed:
                                break
                            await self._admit(tasks, entry, request)
                finally:
                    self._closed = True
                    for running in tuple(self._executing.values()):
                        running.task.cancel()
        finally:
            await self._authority.fence()

    async def close(self) -> None:
        self._closed = True
        await self._authority.fence()
        for running in tuple(self._executing.values()):
            running.task.cancel()

    async def _admit(self, tasks: asyncio.TaskGroup, entry: str, request: RelayRequest) -> None:
        if request.use != self._use:
            await self._reject(entry, request, "environment_forbidden")
            return
        existing = self._executing.get(request.request_id)
        if existing is not None:
            if request != existing.request:
                raise RelayStoreError("request_conflict")
            return
        is_control = request.operation in CONTROL_OPERATIONS
        occupied = sum(
            (item.request.operation in CONTROL_OPERATIONS) == is_control for item in self._executing.values()
        )
        if occupied >= (self._control_concurrency if is_control else self._concurrency):
            await self._reject(entry, request, "environment_overloaded")
            return
        try:
            execute = self._prepare(request)
        except (ValueError, TypeError):
            await self._reject(entry, request, "environment_request_invalid")
            return
        except EnvironmentError as error:
            code = "environment_forbidden" if error.code == "environment_forbidden" else "environment_unsupported"
            await self._reject(entry, request, code)
            return
        task = tasks.create_task(self._execute(entry, request, execute), name="environment-relay-operation")
        self._executing[request.request_id] = _Executing(request, task)
        task.add_done_callback(lambda _: self._executing.pop(request.request_id, None))

    def _prepare(self, request: RelayRequest) -> Callable[[], Awaitable[JsonValue]]:
        if request.operation == "operation.cancel":
            cancel = CancelRequest.model_validate(request.payload)

            async def cancel_operation() -> JsonValue:
                target = self._executing.get(cancel.request_id)
                accepted = target is not None and target.request.operation not in CONTROL_OPERATIONS
                if accepted and target is not None:
                    target.task.cancel()
                # This acknowledges a cancellation attempt, never remote termination.
                return {"accepted": accepted}

            return cancel_operation
        if request.operation == "scope.close":
            DomainModel.model_validate(request.payload)

            async def close_scope() -> JsonValue:
                return None

            return close_scope
        return self._dispatch.prepare(request.operation, request.payload)

    def _deadline(self, request: RelayRequest) -> float:
        observed = self._observation
        return observed.request_started_at + (request.deadline_ms - observed.value.now_ms) / 1000

    async def _execute(self, entry: str, request: RelayRequest, execute: Callable[[], Awaitable[JsonValue]]) -> None:
        possible_effect = False
        try:
            if self._deadline(request) <= monotonic():
                await self._reject(entry, request, "environment_timeout")
                return
            evidence = await self._store.start(request, entry)
            if evidence.phase == "completed":
                terminal = evidence.terminal()
                if terminal is None:
                    raise RelayStoreError("outcome_unknown")
                await self._complete(entry, request, terminal)
                return
            if evidence.phase != "started":
                raise RelayStoreError("outcome_unknown")
            self._authority.check(self._use)
            async with asyncio.timeout_at(self._deadline(request)):
                possible_effect = True
                result = await execute()
            terminal = RelayTerminal(request_id=request.request_id, use=request.use, result=result)
        except (asyncio.CancelledError, TimeoutError, DispatchDenied) as error:
            code = (
                "environment_cancelled"
                if isinstance(error, asyncio.CancelledError)
                else "environment_timeout"
                if isinstance(error, TimeoutError)
                else "environment_unavailable"
            )
            terminal = RelayTerminal(
                request_id=request.request_id,
                use=request.use,
                error=RelayFailure(code=code, certainty="unknown" if possible_effect else "not_dispatched"),
            )
        except EnvironmentError as error:
            terminal = RelayTerminal(
                request_id=request.request_id,
                use=request.use,
                error=RelayFailure.from_environment(error),
            )
        await self._complete(entry, request, terminal)
        if request.operation == "scope.close" and terminal.error is None:
            self._closed = True

    async def _reject(self, entry: str, request: RelayRequest, code: str) -> None:
        terminal = RelayTerminal(
            request_id=request.request_id,
            use=request.use,
            error=RelayFailure.model_validate({"code": code, "certainty": "not_dispatched"}),
        )
        await self._complete(entry, request, terminal)

    async def _complete(self, entry: str, request: RelayRequest, terminal: RelayTerminal) -> None:
        for attempt in range(2):
            try:
                await self._store.complete(request, entry, terminal)
                return
            except RelayStoreError as error:
                if error.code != "relay_unavailable" or attempt:
                    raise
