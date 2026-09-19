"""Bounded Control execution with evidence preceding every possible effect."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from time import monotonic
from typing import Protocol

from a13n_harness.providers.environment.models import EnvironmentError
from pydantic import JsonValue

from a13n_service.ids import ObjectId

from ..domain import DomainModel
from .authority import ConnectionIdentity, DispatchAuthority, DispatchDenied, RequestIdentity
from .coordination import ConfirmedObservation
from .relay_protocol import CONTROL_OPERATIONS, RelayFailure, RelayRequest, RelayTerminal
from .relay_storage import ConnectionRelayStore, RelayInputDelivery, RelayStoreError
from .relay_transfers import FileTransferExecution, FileTransferPlan

type RelayExecution = Callable[[], Awaitable[JsonValue]] | FileTransferPlan


class RelayOperationDispatch(Protocol):
    def __call__(self, request: RelayRequest) -> AbstractAsyncContextManager[RelayExecution]: ...


class CancelRequest(DomainModel):
    request_id: ObjectId


@dataclass(slots=True)
class _Executing:
    request: RelayRequest
    task: asyncio.Task[None]
    transfer: FileTransferExecution | None
    inputs: list[tuple[str, RelayInputDelivery]] = field(default_factory=list)


class RelayControlConsumer:
    """One Device connection reader for independently authorized request scopes.

    Dispatch admission follows durable start evidence. Its request-local context
    owns the exact Session or Device read authority, never a connection-wide use.
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
        connection = authority.identity
        if (
            not isinstance(connection, ConnectionIdentity)
            or connection != store.connection
            or observation.value.status != "online"
            or observation.value.connection != connection
        ):
            raise ValueError("Control consumer requires its exact online connection")
        if not 1 <= concurrency <= 128 or not 1 <= control_concurrency <= 16:
            raise ValueError("Control relay concurrency is outside its bounded range")
        self._store = store
        self._authority = authority
        self._connection = connection
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
                        self._authority.check(self._connection)
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
                            self._authority.check(self._connection)
                            if self._closed:
                                break
                            if isinstance(request, RelayInputDelivery):
                                await self._input(entry, request)
                            else:
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
        if request.scope.connection != self._connection:
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
        task = tasks.create_task(self._execute(entry, request), name="environment-relay-operation")
        self._executing[request.request_id] = _Executing(request, task, None)
        task.add_done_callback(lambda _: self._executing.pop(request.request_id, None))

    async def _input(self, entry: str, delivery: RelayInputDelivery) -> None:
        running = self._executing.get(delivery.request.request_id)
        if running is not None:
            if delivery.request != running.request:
                raise RelayStoreError("request_conflict")
            if running.transfer is None:
                # Initial upload credit can arrive while start evidence/admission
                # is in flight. Retain only the existing bounded input window.
                if len(running.inputs) >= self._store.limits.input_window + 1:
                    raise RelayStoreError("request_invalid")
                running.inputs.append((entry, delivery))
                return
            try:
                running.transfer.accept(delivery.frame)
            except EnvironmentError as error:
                if running.transfer.failure is None:
                    running.transfer.failure = error
                    running.task.cancel()
        await self._store.acknowledge_input(entry, delivery)

    def cancel_scope(self, scope: RequestIdentity) -> None:
        for running in tuple(self._executing.values()):
            if running.request.scope == scope and running.request.operation != "scope.close":
                running.task.cancel()

    def _cancel_operation(self, request: RelayRequest) -> JsonValue:
        cancel = CancelRequest.model_validate(request.payload)
        target = self._executing.get(cancel.request_id)
        accepted = (
            target is not None
            and target.request.scope == request.scope
            and target.request.operation not in CONTROL_OPERATIONS
        )
        if accepted and target is not None:
            target.task.cancel()
        # This acknowledges a cancellation attempt, never remote termination.
        return {"accepted": accepted}

    def _deadline(self, request: RelayRequest) -> float:
        observed = self._observation
        return observed.request_started_at + (request.deadline_ms - observed.value.now_ms) / 1000

    async def _execute(self, entry: str, request: RelayRequest) -> None:
        possible_effect = False
        transfer: FileTransferExecution | None = None
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
            self._authority.check(self._connection)
            async with asyncio.timeout_at(self._deadline(request)):
                async with self._dispatch(request) as execute:
                    if isinstance(execute, FileTransferPlan):
                        transfer = execute.bind(self._store, request, entry)
                        running = self._executing[request.request_id]
                        # Deliver the buffered prefix without yielding, so the
                        # reader cannot overtake it with later input frames.
                        for _, delivery in running.inputs:
                            transfer.accept(delivery.frame)
                        running.transfer = transfer
                        for input_entry, delivery in running.inputs:
                            await self._store.acknowledge_input(input_entry, delivery)
                        running.inputs.clear()
                        execute = transfer
                    possible_effect = True
                    if request.operation == "operation.cancel":
                        result = self._cancel_operation(request)
                    else:
                        if request.operation == "scope.close":
                            self.cancel_scope(request.scope)
                        result = await execute()
            terminal = RelayTerminal(
                request_id=request.request_id,
                scope=request.scope,
                result=result,
                transfer=transfer.position if transfer is not None else None,
            )
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
                scope=request.scope,
                error=RelayFailure(code=code, certainty="unknown" if possible_effect else "not_dispatched"),
            )
        except (ValueError, TypeError):
            terminal = RelayTerminal(
                request_id=request.request_id,
                scope=request.scope,
                error=RelayFailure(code="environment_request_invalid", certainty="not_dispatched"),
            )
        except EnvironmentError as error:
            terminal = RelayTerminal(
                request_id=request.request_id,
                scope=request.scope,
                error=RelayFailure.from_environment(error),
            )
        if transfer is not None and transfer.failure is not None:
            terminal = RelayTerminal(
                request_id=request.request_id,
                scope=request.scope,
                error=RelayFailure.from_environment(transfer.failure),
            )
        await self._complete(entry, request, terminal)

    async def _reject(self, entry: str, request: RelayRequest, code: str) -> None:
        terminal = RelayTerminal(
            request_id=request.request_id,
            scope=request.scope,
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
