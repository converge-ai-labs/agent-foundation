"""Async subagents: child runs a run starts, reads, steers, cancels and continues through the Harness tools.

A child is a thread of its own (origin `child`) in the parent's session, started by one message from the tool
call that delegated it and identified by that call, so a recovered parent finds the same child instead of
starting another. The thread keeps the name of the edge that delegated it. The child runs as its own run under
the parent's principal and authority, on the revision its edge pins; its results come back as `child_result`
entries of the parent thread. An execution is one run of a child thread: continuing a finished child starts the
thread's next run.

Every operation first proves the parent's lease, so a stale attempt changes nothing. The child thread is
locked after the parent's run and attempt; no other path locks a parent run while holding a child thread. A
refusal reaches the model as the tool call's failure; only an unavailable dependency or a lost lease ends the
attempt.
"""

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from a13n_harness import RunInputValue
from a13n_harness.capabilities import (
    AsyncDelegateRequest,
    AsyncExecutionView,
    AsyncResumeRequest,
    SubagentCancelRequest,
    SubagentCancelResult,
    SubagentDelegationPlan,
    SubagentExecutionView,
    SubagentInfoRequest,
    SubagentInfoResult,
    SubagentOperator,
    SubagentOperatorContext,
    SubagentSteerRequest,
    SubagentSteerResult,
    SubagentToolCallContext,
    SubagentWaitRequest,
    SubagentWaitResult,
)
from a13n_harness.capabilities.subagents import SubagentStatus
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from a13n_service.infra.db import lock, short_session, transaction
from a13n_service.infra.errors import ServiceError, conflict, invalid, not_found
from a13n_service.runs import inbox
from a13n_service.runs.accept import Source, accept, delegation, start_run
from a13n_service.runs.agent import ResolvedSubagent
from a13n_service.runs.attempts import AttemptControl, Lease, lock_lease, lock_thread_lease
from a13n_service.runs.environments.mounts import adopt_mounts, reserve_primary
from a13n_service.runs.memories.mounts import adopt_memory_mounts
from a13n_service.runs.runtime import Runtime
from a13n_service.runs.schemas import (
    Delivery,
    EnvironmentMount,
    Message,
    MessagePayload,
    RunOptions,
    TextPart,
    UsageLimit,
)
from a13n_service.runs.seal import stop
from a13n_service.runs.tables import InboxEntryRow, RunRow, SessionRow, ThreadRow
from a13n_service.runs.threads import new_thread
from a13n_service.runs.tools import tool_failures
from a13n_service.tenancy.access import principal_for
from a13n_service.tenancy.authorize import ExecutionAuthority, WorkspaceScope

_STATUS: dict[str, SubagentStatus] = {
    "accepted": "running",
    "running": "running",
    # A child waiting for a person is still in progress from its parent's point of view.
    "waiting": "running",
    "completed": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
}
_TEXT_CHARS = 65536
_WAIT_SECONDS, _MAX_WAIT_SECONDS, _POLL_SECONDS = 30.0, 300.0, 0.25


@dataclass(frozen=True, slots=True)
class _Execution:
    thread: ThreadRow
    run: RunRow
    # Runs of the child thread before this one.
    segment: int
    input: str | None


class ChildRuns(SubagentOperator):
    """The operator of one async agent of the attempt's graph; `edges` are that agent's subagents by name."""

    def __init__(self, runtime: Runtime, lease: Lease, control: AttemptControl, edges: Mapping[str, ResolvedSubagent]):
        self.runtime, self.lease, self.control, self.edges = runtime, lease, control, edges

    async def delegate(
        self,
        plan: SubagentDelegationPlan,
        request: AsyncDelegateRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> AsyncExecutionView:
        with tool_failures():
            return await self._delegate(plan, request, tool_call)

    async def _delegate(
        self, plan: SubagentDelegationPlan, request: AsyncDelegateRequest, tool_call: SubagentToolCallContext | None
    ) -> AsyncExecutionView:
        if tool_call is None or tool_call.tool_call_id is None:
            raise invalid("tool_call", "a child is identified by the tool call that delegates it")
        edge = self.edges.get(request.subagent_name)
        if edge is None:
            raise not_found("subagent", request.subagent_name)
        payload = _payload(plan.context.input, request.prompt)
        settings = self.runtime.settings.worker
        async with transaction(self.runtime.storage) as session:
            thread, run, _, _ = await lock_thread_lease(session, self.lease)
            existing = await session.scalar(
                select(ThreadRow).where(
                    ThreadRow.origin_run_id == run.id, ThreadRow.origin_tool_call_id == tool_call.tool_call_id
                )
            )
            if existing is not None:
                # A recovered parent delegating the same call again gets the child it already started.
                return _view(await _first(session, existing))
            if (await delegation(session, thread, run.id)).depth >= settings.child_depth:
                raise conflict("run", run.id, "child_depth_exceeded")
            spawned = await session.scalar(
                select(func.count()).select_from(ThreadRow).where(ThreadRow.origin_run_id == run.id)
            )
            if (spawned or 0) >= settings.child_count:
                raise conflict("run", run.id, "child_count_exceeded")
            owner = await session.get_one(SessionRow, run.session_id)
            child = new_thread(
                owner,
                mcp_headers=run.mcp_headers,
                origin="child",
                origin_thread_id=thread.id,
                origin_run_id=run.id,
                origin_tool_call_id=tool_call.tool_call_id,
                subagent=request.subagent_name,
            )
            session.add(child)
            await session.flush()
            await self._environments(session, child, run, edge)
            await adopt_memory_mounts(session, child, run.memory_mounts)
            # A run's `max_usage` bounds model requests only, so the edge's request limit is all a child run
            # carries; the edge's token and tool-call limits apply to inline delegation.
            limit = plan.usage_limits.request_limit if plan.usage_limits is not None else None
            message = Message(
                delivery="next_run",
                payload=payload,
                agent_id=edge.selection.agent_id,
                agent_revision_id=edge.revision_id,
                options=RunOptions(max_usage=UsageLimit(requests=limit) if limit is not None else None),
            )
            entry = await inbox.append_message(
                session,
                child,
                message,
                principal_id=run.principal_id,
                authority=ExecutionAuthority.model_validate(run.authority),
                request=None,
                control=self.runtime.settings.control,
            )
            source = await Source.load(session, self.runtime, child, entry, "spawned")
            first = await start_run(session, self.runtime, child, source)
            return _view(_Execution(child, first, 0, None))

    async def info(
        self,
        context: SubagentOperatorContext,
        request: SubagentInfoRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentInfoResult:
        with tool_failures():
            executions, total = await self._page(
                request.execution_id, request.execution_offset, request.execution_limit
            )
        return SubagentInfoResult(
            executions=tuple(_full(item) for item in executions),
            execution_offset=request.execution_offset,
            total=total,
            next_offset=_next(request.execution_offset, len(executions), total),
        )

    async def wait(
        self,
        context: SubagentOperatorContext,
        request: SubagentWaitRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentWaitResult:
        """Until every execution of the page has ended, the timeout passes, or the parent must stop or yield.

        The page is read once; the wait polls only its runs' statuses, then reads those runs again.
        """
        timeout = min(request.timeout_seconds or _WAIT_SECONDS, _MAX_WAIT_SECONDS)
        deadline = asyncio.get_running_loop().time() + timeout
        with tool_failures():
            executions, total = await self._page(
                request.execution_id, request.execution_offset, request.execution_limit
            )
            statuses = {item.run.id: item.run.status for item in executions}
            while any(_STATUS[status] == "running" for status in statuses.values()) and not self._leaving():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(_POLL_SECONDS, remaining))
                statuses = await self._statuses(statuses)
            if any(item.run.status != statuses[item.run.id] for item in executions):
                executions = await self._reread(executions)
        return SubagentWaitResult(
            executions=tuple(_full(item) for item in executions),
            execution_offset=request.execution_offset,
            total=total,
            next_offset=_next(request.execution_offset, len(executions), total),
        )

    async def steer(
        self,
        context: SubagentOperatorContext,
        request: SubagentSteerRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentSteerResult:
        with tool_failures():
            async with transaction(self.runtime.storage) as session:
                thread, run = await self._child(session, request.execution_id)
                if thread.archived_at is not None or thread.current_run_id != run.id:
                    return SubagentSteerResult(execution_id=request.execution_id, accepted=False)
                entry = await self._append(session, thread, run, request.message, delivery="steer")
                return SubagentSteerResult(execution_id=request.execution_id, accepted=True, enqueue_id=entry.id)

    async def cancel(
        self,
        context: SubagentOperatorContext,
        request: SubagentCancelRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentCancelResult:
        with tool_failures():
            async with transaction(self.runtime.storage) as session:
                thread, run = await self._child(session, request.execution_id)
                if run.status not in {"accepted", "running"}:
                    return SubagentCancelResult(execution_id=request.execution_id, accepted=False)
                await stop(session, self.runtime, thread, run)
                return SubagentCancelResult(execution_id=request.execution_id, accepted=True)

    async def resume(
        self,
        plan: SubagentDelegationPlan,
        request: AsyncResumeRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> AsyncExecutionView:
        """The child thread's next run, continuing the ended execution's history with a new message."""
        with tool_failures():
            async with transaction(self.runtime.storage) as session:
                thread, run = await self._child(session, request.execution_id)
                if thread.current_run_id is not None or thread.last_run_id != run.id:
                    raise conflict("run", run.id, "execution_not_resumable")
                entry = await self._append(session, thread, run, request.prompt, delivery="next_run")
                source = await Source.load(session, self.runtime, thread, entry, "input")
                successor = await accept(session, self.runtime, thread, explicit=source)
                if successor is None:
                    raise conflict("run", run.id, "execution_not_resumable")
                segment = await _segments(session, thread.id) - 1
                view = _view(_Execution(thread, successor, segment, None))
                return view.model_copy(update={"resumed_from": run.id})

    def _leaving(self) -> bool:
        """The parent must stop, or its worker is draining: a wait returns what it has, so the attempt can end
        or yield at its next boundary instead of outwaiting the drain."""
        return self.control.stopped.is_set() or self.control.handoff.is_set()

    async def _environments(self, session: AsyncSession, child: ThreadRow, run: RunRow, edge: ResolvedSubagent) -> None:
        """A child's environments are decided here, by its edge; its agent's own template never applies."""
        policy = edge.selection.environment
        if policy.mode == "shared":
            await adopt_mounts(session, child, [EnvironmentMount.model_validate(m) for m in run.environment_mounts])
        elif policy.template_id is not None:  # Exactly a dedicated policy names its template.
            scope = WorkspaceScope(run.organization_id, run.workspace_id)
            principal = await principal_for(session, self.runtime.access, run.principal_id, confinement=scope)
            await reserve_primary(
                session,
                principal,
                child,
                template_id=policy.template_id,
                limit=self.runtime.settings.environments.managed_count,
            )

    async def _child(self, session: AsyncSession, run_id: str) -> tuple[ThreadRow, RunRow]:
        """Prove the parent's lease, then lock the child thread and its run; only this thread's children."""
        parent, _, _ = await lock_lease(session, self.lease)
        run = await session.get(RunRow, run_id)
        thread = await lock(session, ThreadRow, run.thread_id) if run is not None else None
        if run is None or thread is None or thread.origin != "child" or thread.origin_thread_id != parent.thread_id:
            raise not_found("execution", run_id)
        locked = await lock(session, RunRow, run.id)
        assert locked is not None
        return thread, locked

    async def _append(
        self, session: AsyncSession, thread: ThreadRow, run: RunRow, text: str, *, delivery: Delivery
    ) -> InboxEntryRow:
        """A message with the run's own agent, revision and options, so a continuation keeps its limits and a
        steer joins it; the options are the frozen ones, which a child run's own messages never change."""
        message = Message(
            delivery=delivery,
            payload=_payload(text, text),
            agent_id=run.agent_id,
            agent_revision_id=run.agent_revision_id,
            options=RunOptions.model_validate(run.options),
        )
        entry = await inbox.append_message(
            session,
            thread,
            message,
            principal_id=run.principal_id,
            authority=ExecutionAuthority.model_validate(run.authority),
            request=None,
            control=self.runtime.settings.control,
        )
        return entry

    async def _page(self, execution_id: str | None, offset: int, limit: int) -> tuple[list[_Execution], int]:
        """One page of the executions of the parent thread's children, in the order the children and their runs
        started, and how many there are: all of them, or the one named."""
        earlier = aliased(RunRow)
        segment = (
            select(func.count())
            .where(
                earlier.thread_id == RunRow.thread_id,
                tuple_(earlier.created_at, earlier.id) < tuple_(RunRow.created_at, RunRow.id),
            )
            .scalar_subquery()
        )
        children = (
            select(RunRow, ThreadRow, segment)
            .join(ThreadRow, ThreadRow.id == RunRow.thread_id)
            .where(ThreadRow.origin == "child", ThreadRow.origin_thread_id == self.lease.thread_id)
        )
        if execution_id is not None:
            children = children.where(RunRow.id == execution_id)
        async with short_session(self.runtime.storage) as session:
            total = await session.scalar(select(func.count()).select_from(children.subquery())) or 0
            if execution_id is not None and total == 0:
                raise not_found("execution", execution_id)
            rows = (
                await session.execute(
                    children.order_by(ThreadRow.created_at, ThreadRow.id, RunRow.created_at, RunRow.id)
                    .offset(offset)
                    .limit(limit)
                )
            ).tuples()
            page = list(rows)
            sources = [run.source_entry_id for run, _, _ in page if run.source_entry_id is not None]
            selected = select(InboxEntryRow.id, InboxEntryRow.payload).where(InboxEntryRow.id.in_(sources))
            payloads = dict((await session.execute(selected)).tuples().all())
        executions = []
        for run, thread, earlier_runs in page:
            payload = payloads.get(run.source_entry_id or "")
            text = MessagePayload.model_validate(payload).text() if payload is not None else None
            executions.append(_Execution(thread, run, earlier_runs, text))
        return executions, total

    async def _statuses(self, run_ids: Iterable[str]) -> dict[str, str]:
        async with short_session(self.runtime.storage) as session:
            rows = await session.execute(select(RunRow.id, RunRow.status).where(RunRow.id.in_(list(run_ids))))
            return dict(rows.tuples().all())

    async def _reread(self, executions: list[_Execution]) -> list[_Execution]:
        """The page's executions with their runs read again."""
        async with short_session(self.runtime.storage) as session:
            ids = [item.run.id for item in executions]
            runs = {run.id: run for run in await session.scalars(select(RunRow).where(RunRow.id.in_(ids)))}
        return [replace(item, run=runs[item.run.id]) for item in executions]


def _view(execution: _Execution) -> AsyncExecutionView:
    run, thread = execution.run, execution.thread
    assert thread.subagent is not None, "every child thread records the edge that delegated it"
    return AsyncExecutionView(
        execution_id=run.id,
        subagent_name=thread.subagent,
        child_definition_id=run.agent_revision_id,
        status=_STATUS[run.status],
        resumed_from=run.parent_run_id if execution.segment > 0 else None,
        failure=run.failure,
        resumable=run.status in {"completed", "failed", "cancelled"},
        thread_id=thread.id,
        child_run_id=run.id,
        segment_index=execution.segment,
    )


def _full(execution: _Execution) -> SubagentExecutionView:
    return SubagentExecutionView(**_view(execution).model_dump(), input=execution.input)


def _payload(value: RunInputValue, fallback: str) -> MessagePayload:
    """The child's input as message text; the Harness renders delegated context as one JSON string."""
    text = value if isinstance(value, str) and value else fallback
    parts = [
        TextPart(type="text", text=text[start : start + _TEXT_CHARS]) for start in range(0, len(text), _TEXT_CHARS)
    ]
    if len(parts) > 32:
        raise invalid("prompt", "the delegated input is too large")
    return MessagePayload(content=tuple(parts))


async def _first(session: AsyncSession, thread: ThreadRow) -> _Execution:
    run = await session.scalar(select(RunRow).where(RunRow.thread_id == thread.id).order_by(RunRow.created_at))
    if run is None:
        raise ServiceError("internal", "A child thread has no run")
    return _Execution(thread, run, 0, None)


async def _segments(session: AsyncSession, thread_id: str) -> int:
    return (await session.scalar(select(func.count()).select_from(RunRow).where(RunRow.thread_id == thread_id))) or 0


def _next(offset: int, count: int, total: int) -> int | None:
    return offset + count if offset + count < total else None
