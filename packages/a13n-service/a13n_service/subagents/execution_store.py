"""Authorized read model for child executions visible to one parent Attempt."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from a13n_harness.capabilities import (
    AsyncExecutionView,
    SubagentActivitySnapshot,
    SubagentExecutionView,
    SubagentOperatorContext,
    SubagentStatus,
)
from pydantic import JsonValue, ValidationError
from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from a13n_service.agents.models import AgentRevisionRecord
from a13n_service.iam import WorkspaceAction
from a13n_service.iam.operation import authorization_operation
from a13n_service.interactions.attempts import AttemptContext, read_attempt_authority
from a13n_service.interactions.domain import Run, RunStatus, Thread
from a13n_service.interactions.input import AcceptedAgentInput, TextContent
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, assume_utc, utc_now

from .authorization import ChildRunAuthorizationError, authorize_parent_child_action
from .domain import ChildRunRelationship
from .models import ChildRunRelationshipRecord

ACTIVITY_OUTPUT_PREVIEW_LIMIT = 32 * 1024


class SubagentOperatorError(RuntimeError):
    """A bounded failure at the Host-owned asynchronous subagent boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AttemptAuthoritySource(Protocol):
    """Expose the latest versions for one process-local Attempt authority."""

    @property
    def current_context(self) -> AttemptContext: ...


@dataclass(frozen=True, slots=True)
class RetainedChildExecution:
    """Authorized relational facts for one durable child execution segment."""

    relationship: ChildRunRelationship
    parent_run: Run
    run: Run
    thread: Thread
    resumed_from_relationship_id: str | None
    child_definition_id: str
    segment_index: int
    input: str


@dataclass(frozen=True, slots=True)
class ExecutionPage:
    items: tuple[RetainedChildExecution, ...]
    offset: int
    total: int

    def next_offset(self, limit: int) -> int | None:
        consumed = self.offset + len(self.items)
        return consumed if len(self.items) == limit and consumed < self.total else None


class SubagentExecutionStore:
    """Query scope-visible child executions and reauthorize every projection."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        authority: AttemptAuthoritySource,
        *,
        parent_context: Callable[[], SubagentOperatorContext],
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._authority = authority
        self._parent_context = parent_context
        self._clock = clock

    def require_context(self, context: SubagentOperatorContext) -> AttemptContext:
        authority = self._authority.current_context
        expected = self._parent_context()
        if context != expected:
            raise SubagentOperatorError(
                "subagent_parent_context_mismatch",
                "Subagent operation crossed its parent logical Run boundary",
            )
        return authority

    async def read_exact(
        self,
        context: SubagentOperatorContext,
        execution_id: str,
        action: WorkspaceAction,
    ) -> RetainedChildExecution:
        page = await self.read_page(
            context,
            execution_id=execution_id,
            offset=0,
            limit=1,
            action=action,
        )
        if not page.items:
            raise SubagentOperatorError(
                "subagent_execution_not_found",
                "Subagent execution was not found in the visible parent scope",
            )
        return page.items[0]

    @authorization_operation
    async def read_page(
        self,
        context: SubagentOperatorContext,
        *,
        execution_id: str | None = None,
        offset: int,
        limit: int,
        action: WorkspaceAction,
    ) -> ExecutionPage:
        authority = self.require_context(context)
        query_offset = 0 if execution_id is not None else offset
        async with short_session(self._sessions) as database:
            parent, _, _ = await read_attempt_authority(database, authority, assume_utc(self._clock()))
            session = await _require_session(database, parent)
            origin_parent = aliased(RunRecord)
            filters = [
                ChildRunRelationshipRecord.organization_id == authority.organization_id,
                origin_parent.session_id == parent.session_id,
                or_(
                    origin_parent.thread_id == parent.thread_id,
                    ChildRunRelationshipRecord.result_visibility == "session",
                ),
            ]
            if execution_id is not None:
                filters.append(ChildRunRelationshipRecord.id == execution_id)
            relationship_scope = and_(
                origin_parent.organization_id == ChildRunRelationshipRecord.organization_id,
                origin_parent.id == ChildRunRelationshipRecord.parent_run_id,
            )
            total = int(
                await database.scalar(
                    select(func.count())
                    .select_from(ChildRunRelationshipRecord)
                    .join(origin_parent, relationship_scope)
                    .where(*filters)
                )
                or 0
            )
            rows = tuple(
                (
                    await database.scalars(
                        select(ChildRunRelationshipRecord)
                        .join(origin_parent, relationship_scope)
                        .where(*filters)
                        .order_by(ChildRunRelationshipRecord.created_at, ChildRunRelationshipRecord.id)
                        .offset(query_offset)
                        .limit(limit)
                    )
                ).all()
            )
            executions = await _load_executions(database, rows)
            try:
                await authorize_parent_child_action(
                    database,
                    parent=parent.to_resource(),
                    source_parent_agent_ids=tuple(sorted({item.parent_run.agent_id for item in executions})),
                    child_agent_ids=tuple(sorted({item.run.agent_id for item in executions})),
                    workspace_id=session.workspace_id,
                    action=action,
                )
            except ChildRunAuthorizationError as error:
                raise SubagentOperatorError(
                    "subagent_authorization_denied",
                    "Persisted parent Principal is no longer authorized for this subagent operation",
                ) from error
        return ExecutionPage(items=executions, offset=query_offset, total=total)


async def _load_executions(
    database: AsyncSession,
    relationships: tuple[ChildRunRelationshipRecord, ...],
) -> tuple[RetainedChildExecution, ...]:
    if not relationships:
        return ()
    organization_id = relationships[0].organization_id
    parent_ids = tuple({row.parent_run_id for row in relationships})
    parent_runs = tuple(
        (
            await database.scalars(
                select(RunRecord).where(RunRecord.organization_id == organization_id, RunRecord.id.in_(parent_ids))
            )
        ).all()
    )
    child_ids = tuple(row.child_run_id for row in relationships)
    child_runs = tuple(
        (
            await database.scalars(
                select(RunRecord).where(RunRecord.organization_id == organization_id, RunRecord.id.in_(child_ids))
            )
        ).all()
    )
    thread_ids = tuple({row.child_thread_id for row in relationships})
    threads = tuple(
        (
            await database.scalars(
                select(ThreadRecord).where(
                    ThreadRecord.organization_id == organization_id, ThreadRecord.id.in_(thread_ids)
                )
            )
        ).all()
    )
    source_child_ids = tuple({row.parent_run_id for row in child_runs if row.parent_run_id is not None})
    source_relationships = tuple(
        (
            await database.scalars(
                select(ChildRunRelationshipRecord).where(
                    ChildRunRelationshipRecord.organization_id == organization_id,
                    ChildRunRelationshipRecord.child_run_id.in_(source_child_ids),
                )
            )
        ).all()
        if source_child_ids
        else ()
    )
    segment_by_child = await _load_segment_indexes(database, organization_id=organization_id, child_ids=child_ids)
    revision_ids = tuple({row.agent_revision_id for row in child_runs})
    revisions = tuple(
        (
            await database.scalars(
                select(AgentRevisionRecord).where(
                    AgentRevisionRecord.organization_id == organization_id,
                    AgentRevisionRecord.id.in_(revision_ids),
                )
            )
        ).all()
    )
    children_by_id = {row.id: row for row in child_runs}
    parents_by_id = {row.id: row for row in parent_runs}
    threads_by_id = {row.id: row for row in threads}
    relationships_by_child = {row.child_run_id: row.id for row in source_relationships}
    revisions_by_id = {row.id: row for row in revisions}
    result: list[RetainedChildExecution] = []
    for relationship_record in relationships:
        parent = parents_by_id.get(relationship_record.parent_run_id)
        child = children_by_id.get(relationship_record.child_run_id)
        thread = threads_by_id.get(relationship_record.child_thread_id)
        revision = (
            None if child is None or child.agent_revision_id is None else revisions_by_id.get(child.agent_revision_id)
        )
        if parent is None or child is None or thread is None or revision is None:
            raise SubagentOperatorError(
                "subagent_execution_corrupt",
                "Subagent relationship lost its parent Run, child Run, or Thread",
            )
        child_resource = child.to_resource()
        resumed_from = relationships_by_child.get(child.parent_run_id) if child.parent_run_id is not None else None
        segment_index = segment_by_child.get(child.id)
        if segment_index is None:
            raise SubagentOperatorError(
                "subagent_execution_corrupt",
                "Subagent relationship is missing its continuation position",
            )
        result.append(
            RetainedChildExecution(
                relationship=relationship_record.to_resource(),
                parent_run=parent.to_resource(),
                run=child_resource,
                thread=thread.to_resource(),
                resumed_from_relationship_id=resumed_from,
                child_definition_id=f"agent-config-{revision.content_digest[:24]}",
                segment_index=segment_index,
                input=execution_input(child_resource),
            )
        )
    return tuple(result)


async def _load_segment_indexes(
    database: AsyncSession,
    *,
    organization_id: str,
    child_ids: tuple[str, ...],
) -> dict[str, int]:
    lineage = select(
        RunRecord.id.label("target_run_id"),
        RunRecord.id.label("run_id"),
        RunRecord.parent_run_id.label("parent_run_id"),
        RunRecord.thread_id.label("thread_id"),
        literal(0).label("depth"),
    ).where(RunRecord.organization_id == organization_id, RunRecord.id.in_(child_ids))
    ancestors = lineage.cte("subagent_execution_lineage", recursive=True)
    parent = aliased(RunRecord)
    ancestors = ancestors.union_all(
        select(
            ancestors.c.target_run_id,
            parent.id,
            parent.parent_run_id,
            parent.thread_id,
            ancestors.c.depth + 1,
        ).join(
            parent,
            and_(
                parent.organization_id == organization_id,
                parent.id == ancestors.c.parent_run_id,
                parent.thread_id == ancestors.c.thread_id,
            ),
        )
    )
    rows = tuple(
        (
            await database.execute(
                select(ancestors.c.target_run_id, func.max(ancestors.c.depth).label("segment_index")).group_by(
                    ancestors.c.target_run_id
                )
            )
        ).all()
    )
    return {str(row.target_run_id): int(row.segment_index) for row in rows}


async def _require_session(database: AsyncSession, parent: RunRecord) -> SessionRecord:
    session = await database.scalar(
        select(SessionRecord).where(
            SessionRecord.organization_id == parent.organization_id,
            SessionRecord.id == parent.session_id,
        )
    )
    if session is None:
        raise SubagentOperatorError(
            "subagent_session_missing",
            "Parent Session was not found",
        )
    return session


def execution_input(run: Run) -> str:
    try:
        accepted = AcceptedAgentInput.model_validate(run.input)
    except ValidationError as error:
        raise SubagentOperatorError(
            "subagent_execution_corrupt",
            "Subagent Run has invalid accepted input",
        ) from error
    if len(accepted.content) != 1 or not isinstance(accepted.content[0], TextContent):
        raise SubagentOperatorError(
            "subagent_execution_corrupt",
            "Subagent Run does not retain one delegated text context",
        )
    return accepted.content[0].text


def is_resumable(execution: RetainedChildExecution) -> bool:
    return (
        execution.run.status is RunStatus.completed
        and execution.thread.current_run_id == execution.run.id
        and execution.thread.head_run_id == execution.run.id
    )


def compact_execution_view(execution: RetainedChildExecution) -> AsyncExecutionView:
    return AsyncExecutionView(
        execution_id=execution.relationship.id,
        subagent_name=execution.relationship.subagent_name,
        child_definition_id=execution.child_definition_id,
        status=_status(execution.run.status),
        resumed_from=execution.resumed_from_relationship_id,
        failure=_failure(execution.run),
        resumable=is_resumable(execution),
        thread_id=execution.run.thread_id,
        child_run_id=execution.run.id,
        segment_index=execution.segment_index,
    )


def full_execution_view(execution: RetainedChildExecution) -> SubagentExecutionView:
    return SubagentExecutionView(
        **compact_execution_view(execution).model_dump(mode="python"),
        input=execution.input,
        activity=_activity(execution.run),
    )


def _status(status: RunStatus) -> SubagentStatus:
    if status in {RunStatus.accepted, RunStatus.running, RunStatus.waiting}:
        return "running"
    if status is RunStatus.completed:
        return "succeeded"
    if status is RunStatus.failed:
        return "failed"
    return "cancelled"


def _failure(run: Run) -> JsonValue | None:
    return None if run.failure is None else run.failure.model_dump(mode="json", by_alias=True)


def _activity(run: Run) -> SubagentActivitySnapshot | None:
    if run.status is not RunStatus.completed or run.output_text is None:
        return None
    sealed_state = run.sealed_state
    if sealed_state is None:
        raise SubagentOperatorError(
            "subagent_execution_corrupt",
            "Completed subagent Run is missing its sealed checkpoint",
        )
    output_preview = run.output_text[:ACTIVITY_OUTPUT_PREVIEW_LIMIT]
    return SubagentActivitySnapshot(
        sequence=sealed_state.checkpoint_seq,
        output_preview=output_preview,
        output_truncated=len(run.output_text) > len(output_preview),
    )


__all__ = [
    "ACTIVITY_OUTPUT_PREVIEW_LIMIT",
    "AttemptAuthoritySource",
    "ExecutionPage",
    "RetainedChildExecution",
    "SubagentExecutionStore",
    "SubagentOperatorError",
    "compact_execution_view",
    "execution_input",
    "full_execution_view",
    "is_resumable",
]
