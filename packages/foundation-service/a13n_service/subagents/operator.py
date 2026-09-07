"""Attempt-scoped implementation of the Harness asynchronous subagent boundary."""

from __future__ import annotations

from collections.abc import Callable

from a13n_harness import SafeFailure
from a13n_harness.capabilities import (
    AsyncDelegateRequest,
    AsyncExecutionView,
    AsyncResumeRequest,
    SubagentCancelRequest,
    SubagentCancelResult,
    SubagentDelegationPlan,
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
from a13n_harness.usage import intersect_usage_limits
from anyio import current_time, sleep
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import WorkspaceAction
from a13n_service.interactions.attempts import AttemptContext
from a13n_service.interactions.domain import RunStatus
from a13n_service.interactions.inbox import ThreadInboxStore
from a13n_service.interactions.inbox_persistence import ThreadInboxConflict
from a13n_service.interactions.input import AcceptedAgentInput, TextContent
from a13n_service.interactions.outcomes import RunOutcomeError, RunOutcomeService
from a13n_service.temporal import Clock, utc_now

from .acceptance import ChildRunAcceptanceReceipt, ChildRunAcceptanceService
from .admission import ChildRunAdmissionPreparer
from .execution_store import (
    AttemptAuthoritySource,
    RetainedChildExecution,
    SubagentExecutionStore,
    SubagentOperatorError,
    compact_execution_view,
    execution_input,
    full_execution_view,
    is_resumable,
)
from .preparation import PreparedChildRunAcceptance, PreparedChildRunResume

_ACTIVE_STATUSES = {RunStatus.accepted, RunStatus.running}
_STEERABLE_STATUSES = {*_ACTIVE_STATUSES, RunStatus.waiting}
_TERMINAL_STATUSES = {RunStatus.completed, RunStatus.failed, RunStatus.cancelled}


class DurableSubagentOperator(SubagentOperator):
    """Implement standard Harness async tools over durable Foundation authority."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        authority: AttemptAuthoritySource,
        admission_preparer: ChildRunAdmissionPreparer,
        acceptance: ChildRunAcceptanceService,
        inbox: ThreadInboxStore,
        outcomes: RunOutcomeService,
        *,
        parent_context: Callable[[], SubagentOperatorContext],
        default_wait_timeout_seconds: float = 30.0,
        max_wait_timeout_seconds: float = 300.0,
        wait_poll_interval_seconds: float = 0.1,
        clock: Clock = utc_now,
    ) -> None:
        if not 0 < default_wait_timeout_seconds <= max_wait_timeout_seconds:
            raise ValueError("default subagent wait timeout must fit the maximum")
        if wait_poll_interval_seconds <= 0:
            raise ValueError("subagent wait poll interval must be positive")
        self._admission_preparer = admission_preparer
        self._acceptance = acceptance
        self._inbox = inbox
        self._outcomes = outcomes
        self._executions = SubagentExecutionStore(
            sessions,
            authority,
            parent_context=parent_context,
            clock=clock,
        )
        self._default_wait_timeout_seconds = default_wait_timeout_seconds
        self._max_wait_timeout_seconds = max_wait_timeout_seconds
        self._wait_poll_interval_seconds = wait_poll_interval_seconds

    async def delegate(
        self,
        plan: SubagentDelegationPlan,
        request: AsyncDelegateRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> AsyncExecutionView:
        del tool_call
        authority = self._require_plan(plan, request.subagent_name)
        delegated_input = _delegated_input(plan)
        prepared = await self._admission_preparer.prepare_delegate(authority, plan, request, delegated_input)
        _validate_delegate_candidate(prepared, authority=authority, plan=plan, delegated_input=delegated_input)
        receipt = await self._acceptance.accept(prepared, authority)
        return _accepted_view(
            receipt,
            child_definition_id=prepared.child_definition_id,
            resumed_from=None,
            segment_index=0,
        )

    async def info(
        self,
        context: SubagentOperatorContext,
        request: SubagentInfoRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentInfoResult:
        del tool_call
        page = await self._executions.read_page(
            context,
            execution_id=request.execution_id,
            offset=request.execution_offset,
            limit=request.execution_limit,
            action=WorkspaceAction.run_read,
        )
        return SubagentInfoResult(
            executions=tuple(full_execution_view(item) for item in page.items),
            execution_offset=page.offset,
            total=page.total,
            next_offset=page.next_offset(request.execution_limit),
        )

    async def wait(
        self,
        context: SubagentOperatorContext,
        request: SubagentWaitRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentWaitResult:
        del tool_call
        timeout = min(
            request.timeout_seconds or self._default_wait_timeout_seconds,
            self._max_wait_timeout_seconds,
        )
        deadline = current_time() + timeout
        while True:
            page = await self._executions.read_page(
                context,
                execution_id=request.execution_id,
                offset=request.execution_offset,
                limit=request.execution_limit,
                action=WorkspaceAction.run_read,
            )
            if not page.items or all(item.run.status in _TERMINAL_STATUSES for item in page.items):
                break
            remaining = deadline - current_time()
            if remaining <= 0:
                break
            await sleep(min(self._wait_poll_interval_seconds, remaining))
        return SubagentWaitResult(
            executions=tuple(full_execution_view(item) for item in page.items),
            execution_offset=page.offset,
            total=page.total,
            next_offset=page.next_offset(request.execution_limit),
        )

    async def steer(
        self,
        context: SubagentOperatorContext,
        request: SubagentSteerRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentSteerResult:
        del tool_call
        execution = await self._executions.read_exact(context, request.execution_id, WorkspaceAction.run_steer)
        if execution.run.status not in _STEERABLE_STATUSES or execution.thread.current_run_id != execution.run.id:
            return SubagentSteerResult(execution_id=request.execution_id, accepted=False)
        try:
            receipt = await self._inbox.append_steer(
                organization_id=execution.run.organization_id,
                run_id=execution.run.id,
                input=AcceptedAgentInput(
                    schema_version="1",
                    content=(TextContent(text=request.message),),
                ),
            )
        except ThreadInboxConflict as error:
            current = await self._executions.read_exact(context, request.execution_id, WorkspaceAction.run_steer)
            if current.run.status not in _STEERABLE_STATUSES or current.thread.current_run_id != current.run.id:
                return SubagentSteerResult(execution_id=request.execution_id, accepted=False)
            raise SubagentOperatorError(
                "subagent_steer_conflict",
                "Subagent steering lost a concurrent Thread mutation",
            ) from error
        return SubagentSteerResult(
            execution_id=request.execution_id,
            accepted=True,
            enqueue_id=receipt.steer_id,
        )

    async def cancel(
        self,
        context: SubagentOperatorContext,
        request: SubagentCancelRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> SubagentCancelResult:
        del tool_call
        execution = await self._executions.read_exact(context, request.execution_id, WorkspaceAction.run_interrupt)
        if execution.run.status not in _ACTIVE_STATUSES or execution.thread.current_run_id != execution.run.id:
            return SubagentCancelResult(
                execution_id=request.execution_id,
                accepted=False,
                status=compact_execution_view(execution).status,
            )
        try:
            await self._outcomes.cancel(
                organization_id=execution.run.organization_id,
                run_id=execution.run.id,
                expected_run_version=execution.run.version,
                expected_thread_version=execution.thread.version,
                failure=SafeFailure(
                    code="subagent_cancelled_by_parent",
                    message="The parent Agent requested cancellation of this subagent.",
                ),
            )
        except RunOutcomeError as error:
            current = await self._executions.read_exact(context, request.execution_id, WorkspaceAction.run_interrupt)
            if current.run.status in _TERMINAL_STATUSES:
                return SubagentCancelResult(
                    execution_id=request.execution_id,
                    accepted=False,
                    status=compact_execution_view(current).status,
                )
            raise SubagentOperatorError(
                "subagent_cancel_conflict",
                "Subagent cancellation lost a concurrent Run mutation",
            ) from error
        return SubagentCancelResult(
            execution_id=request.execution_id,
            accepted=True,
            status="cancelled",
        )

    async def resume(
        self,
        plan: SubagentDelegationPlan,
        request: AsyncResumeRequest,
        *,
        tool_call: SubagentToolCallContext | None = None,
    ) -> AsyncExecutionView:
        del tool_call
        authority = self._require_plan(plan, plan.child.declaration.name)
        delegated_input = _delegated_input(plan)
        source = await self._executions.read_exact(
            plan.parent,
            request.execution_id,
            WorkspaceAction.run_continue,
        )
        if not is_resumable(source):
            raise SubagentOperatorError(
                "subagent_not_resumable",
                "The retained subagent execution is not a selected completed child head",
            )
        prepared = await self._admission_preparer.prepare_resume(
            authority,
            source,
            plan,
            request,
            delegated_input,
        )
        _validate_resume_candidate(
            prepared,
            authority=authority,
            source=source,
            plan=plan,
            delegated_input=delegated_input,
        )
        receipt = await self._acceptance.accept_resume(prepared, authority)
        return _accepted_view(
            receipt,
            child_definition_id=prepared.child_definition_id,
            resumed_from=source.relationship.id,
            segment_index=source.segment_index + 1,
        )

    def _require_plan(self, plan: SubagentDelegationPlan, subagent_name: str) -> AttemptContext:
        authority = self._executions.require_context(plan.parent)
        if plan.child.declaration.name != subagent_name:
            raise SubagentOperatorError(
                "subagent_plan_invalid",
                "Harness child plan identity is inconsistent",
            )
        return authority


def _validate_delegate_candidate(
    prepared: PreparedChildRunAcceptance,
    *,
    authority: AttemptContext,
    plan: SubagentDelegationPlan,
    delegated_input: str,
) -> None:
    if (
        prepared.relationship.parent_run_id != authority.run_id
        or prepared.relationship.subagent_name != plan.child.declaration.name
        or prepared.run.parent_agent_instance_id != plan.parent.parent_agent_instance_id
        or execution_input(prepared.run) != delegated_input
        or prepared.child_definition_id != plan.child.definition.definition_id
        or intersect_usage_limits(prepared.state.usage_limits, plan.usage_limits) != prepared.state.usage_limits
    ):
        raise SubagentOperatorError(
            "subagent_admission_candidate_invalid",
            "Prepared child admission does not match the Harness plan",
        )


def _validate_resume_candidate(
    prepared: PreparedChildRunResume,
    *,
    authority: AttemptContext,
    source: RetainedChildExecution,
    plan: SubagentDelegationPlan,
    delegated_input: str,
) -> None:
    if (
        prepared.resumed_from_relationship_id != source.relationship.id
        or prepared.resumed_from_child_run_id != source.run.id
        or prepared.relationship.parent_run_id != authority.run_id
        or prepared.relationship.subagent_name != plan.child.declaration.name
        or prepared.run.parent_agent_instance_id != plan.parent.parent_agent_instance_id
        or execution_input(prepared.run) != delegated_input
        or prepared.child_definition_id != source.child_definition_id
        or intersect_usage_limits(prepared.state.usage_limits, plan.usage_limits) != prepared.state.usage_limits
    ):
        raise SubagentOperatorError(
            "subagent_resume_candidate_invalid",
            "Prepared child continuation does not match the Harness plan",
        )


def _accepted_view(
    receipt: ChildRunAcceptanceReceipt,
    *,
    child_definition_id: str,
    resumed_from: str | None,
    segment_index: int,
) -> AsyncExecutionView:
    relationship = receipt.relationship
    return AsyncExecutionView(
        execution_id=relationship.id,
        subagent_name=relationship.subagent_name,
        child_definition_id=child_definition_id,
        status="running",
        resumed_from=resumed_from,
        thread_id=receipt.child_thread_id,
        child_run_id=receipt.child_run_id,
        segment_index=segment_index,
    )


def _delegated_input(plan: SubagentDelegationPlan) -> str:
    value = plan.context.input
    if not isinstance(value, str) or not value:
        raise SubagentOperatorError(
            "subagent_input_invalid",
            "Foundation asynchronous delegation requires the Harness JSON context projection",
        )
    return value


__all__ = [
    "DurableSubagentOperator",
]
