"""Worker hosting of the Harness executor: resources, cross-attempt barriers and shared controls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from functools import cached_property
from typing import Any

from a13n_harness.environment.models import EnvironmentError
from a13n_harness.tool_execution import (
    RetainedScope,
    StoredToolExecution,
    ToolExecutionRecord,
    ToolExecutionRuntime,
    current_execution,
)
from a13n_harness.toolsets.shell_control import ShellControl, ShellTarget
from pydantic import JsonValue
from sqlalchemy import select

from a13n_service.infra.db import lock, now, short_session, transaction
from a13n_service.runs.attempts import AuthorityRevoked, Lease, LeaseLost, authorize_execution
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.tables import AttemptRow, ToolExecutionRow
from a13n_service.runs.tool_executions.authority import authorize_control, lock_execution, prove
from a13n_service.runs.tool_executions.routing import Caller, Request, Routing
from a13n_service.runs.tool_executions.store import ORIGIN, SEQUENTIAL, Store

_RESOURCES: ContextVar[RetainedScope[Any]] = ContextVar("service_tool_resources")


@asynccontextmanager
async def resources[T](lease: Lease, manager: AbstractAsyncContextManager[T]) -> AsyncIterator[T]:
    """Keep task-affine clients alive until the last original invocation releases them."""
    origin = ORIGIN.set(lease)
    scope = RetainedScope(manager)
    token = _RESOURCES.set(scope)
    try:
        yield await scope.ready()
    finally:
        _RESOURCES.reset(token)
        ORIGIN.reset(origin)
        await scope.close()


class Executions(ToolExecutionRuntime):
    """Host adapter, not another executor. Harness owns admission, tasks, results and cancellation."""

    def __init__(self, runtime: Runtime):
        super().__init__(store=Store(runtime), max_active=runtime.settings.tool_executions.max_active)
        self.runtime = runtime
        self._origins: dict[str, Lease] = {}

    @cached_property
    def routing(self) -> Routing:
        return Routing(self)

    def admit(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        thread_id: str,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        release: Callable[[], Awaitable[None]] | None = None,
        sequential: bool = False,
    ) -> ToolExecutionRecord:
        host_release: Callable[[], Awaitable[None]] | None = None
        origin = ORIGIN.get()

        async def dispatch() -> Any:
            await self._predecessors(thread_id, sequential=sequential)
            await prove(self.runtime, ORIGIN.get())
            return await operation()

        async def cleanup() -> None:
            try:
                if release is not None:
                    await release()
            finally:
                self._origins.pop(record.execution_id, None)
                if host_release is not None:
                    await host_release()

        token = SEQUENTIAL.set(sequential)
        try:
            record = super().admit(
                dispatch,
                thread_id=thread_id,
                run_id=run_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                release=cleanup,
                sequential=sequential,
            )
            self._origins[record.execution_id] = origin
            # No await between admission and retaining the scope; failure to admit retains nothing.
            scope = _RESOURCES.get(None)
            if scope is not None:
                host_release = scope.retain()
            return record
        finally:
            SEQUENTIAL.reset(token)

    async def inline(self, operation: Callable[[], Awaitable[Any]], *, thread_id: str, sequential: bool) -> Any:
        async def dispatch() -> Any:
            await self._predecessors(thread_id, sequential=sequential)
            return await operation()

        return await super().inline(dispatch, thread_id=thread_id, sequential=sequential)

    async def _predecessors(self, thread_id: str, *, sequential: bool) -> None:
        lease = ORIGIN.get()
        # Service serializes attempts of a Thread. The Harness orders this attempt locally; only older
        # retained attempts can overlap it, so no distributed queue or execution replay is needed.
        while True:
            async with short_session(self.runtime.storage) as session:
                origin_created = (
                    select(AttemptRow.created_at).where(AttemptRow.id == lease.attempt_id).scalar_subquery()
                )
                query = (
                    select(ToolExecutionRow.id)
                    .join(AttemptRow, AttemptRow.id == ToolExecutionRow.attempt_id)
                    .where(
                        ToolExecutionRow.thread_id == thread_id,
                        AttemptRow.created_at < origin_created,
                        ToolExecutionRow.completed_at.is_(None),
                    )
                )
                if not sequential:
                    query = query.where(ToolExecutionRow.sequential.is_(True))
                pending = await session.scalar(query.limit(1))
            if pending is None:
                return
            await asyncio.sleep(0.1)

    async def call_resource(
        self, thread_id: str, execution_id: str, request: dict[str, JsonValue]
    ) -> dict[str, JsonValue] | None:
        lease = ORIGIN.get()
        if lease.thread_id != thread_id:
            return None
        await prove(self.runtime, lease)
        control = ShellControl.model_validate(request)
        async with short_session(self.runtime.storage) as session:
            row = await session.get(ToolExecutionRow, execution_id)
            if row is None or row.thread_id != thread_id or row.completed_at is not None:
                return None
            worker_id = row.worker_id
        current = current_execution()
        envelope = Request(
            thread_id=thread_id,
            execution_id=execution_id,
            caller=Caller(
                run_id=lease.run_id,
                attempt_id=lease.attempt_id,
                worker_id=lease.worker_id,
                execution_id=current.execution_id if current is not None else None,
            ),
            control=control,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=control.timeout_seconds + self.runtime.settings.environments.wait_seconds),
        )
        if execution_id in self._origins:
            return await self.local_control(envelope)
        return await self.routing.call(worker_id, envelope)

    async def local_control(self, request: Request) -> dict[str, JsonValue] | None:
        origin = self._origins.get(request.execution_id)
        if origin is None or origin.thread_id != request.thread_id:
            return None
        description = await super().call_resource(request.thread_id, request.execution_id, {"operation": "describe"})
        if description is None:
            return None
        target = ShellTarget.model_validate(description)
        try:
            async with transaction(self.runtime.storage) as session:
                run, _, current = await lock_execution(session, origin, request.execution_id)
                if request.expires_at <= current:
                    raise LeaseLost()
                await authorize_execution(session, self.runtime.access, run)
                await authorize_control(
                    session,
                    self.runtime,
                    request.caller,
                    thread_id=request.thread_id,
                    environment_id=target.identity.environment_id,
                    actions=request.control.actions,
                )
        except (LeaseLost, AuthorityRevoked):
            raise EnvironmentError(
                "Shell control authority is no longer current.",
                code="environment_denied",
                details={"dispatch_stage": "pre_dispatch"},
            ) from None
        if request.control.operation == "describe":
            return description
        return await super().call_resource(
            request.thread_id, request.execution_id, request.control.model_dump(mode="json")
        )

    async def cancel(self, thread_id: str, execution_id: str) -> ToolExecutionRecord | None:
        async with transaction(self.runtime.storage) as session:
            row = await lock(session, ToolExecutionRow, execution_id)
            if row is not None and row.thread_id == thread_id and row.completed_at is None:
                row.cancel_requested_at = row.cancel_requested_at or await now(session)
        return await super().cancel(thread_id, execution_id)

    async def wait(
        self, thread_id: str, execution_id: str, *, timeout_seconds: float | None = None
    ) -> StoredToolExecution | None:
        deadline = None if timeout_seconds is None else asyncio.get_running_loop().time() + timeout_seconds
        while True:
            value = await super().wait(thread_id, execution_id, timeout_seconds=0)
            if value is None or value.record.terminal:
                return value
            remaining = None if deadline is None else deadline - asyncio.get_running_loop().time()
            if remaining is not None and remaining <= 0:
                return value
            await asyncio.sleep(min(0.1, remaining) if remaining is not None else 0.1)
