"""Detached Run and Thread observations for command preparation, never commit authority."""

from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .domain import Run, Thread
from .errors import command_not_found
from .models import RunRecord, SessionRecord, ThreadRecord
from .session_scope import SessionScope


@dataclass(frozen=True, slots=True)
class RunSource:
    run: Run
    thread: Thread
    session_scope: SessionScope

    def require_scope(self, *, workspace_id: str, run_id: str) -> None:
        if (
            self.run.id != run_id
            or self.run.thread_id != self.thread.id
            or self.run.session_id != self.thread.session_id
            or self.run.organization_id != self.thread.organization_id
        ):
            raise command_not_found()
        ThreadSource(self.thread, None, self.session_scope).require_scope(
            workspace_id=workspace_id, thread_id=self.thread.id
        )


@dataclass(frozen=True, slots=True)
class ThreadSource:
    thread: Thread
    current: Run | None
    session_scope: SessionScope

    def require_scope(self, *, workspace_id: str, thread_id: str) -> None:
        if (
            self.thread.id != thread_id
            or self.session_scope.workspace_id != workspace_id
            or self.thread.session_id != self.session_scope.id
            or self.thread.organization_id != self.session_scope.organization_id
            or (
                self.current is not None
                and (
                    self.current.id != self.thread.current_run_id
                    or self.current.thread_id != self.thread.id
                    or self.current.session_id != self.thread.session_id
                    or self.current.organization_id != self.thread.organization_id
                )
            )
        ):
            raise command_not_found()


async def load_run_source(database: AsyncSession, *, workspace_id: str, run_id: str) -> RunSource:
    row = (
        await database.execute(
            select(RunRecord, ThreadRecord, SessionRecord)
            .select_from(RunRecord)
            .join(
                SessionRecord,
                and_(
                    SessionRecord.organization_id == RunRecord.organization_id,
                    SessionRecord.id == RunRecord.session_id,
                ),
            )
            .join(
                ThreadRecord,
                and_(
                    ThreadRecord.organization_id == RunRecord.organization_id,
                    ThreadRecord.session_id == RunRecord.session_id,
                    ThreadRecord.id == RunRecord.thread_id,
                ),
            )
            .where(RunRecord.id == run_id, SessionRecord.workspace_id == workspace_id)
        )
    ).one_or_none()
    if row is None:
        raise command_not_found()
    run, thread, conversation = row
    return RunSource(run.to_resource(), thread.to_resource(), SessionScope.from_record(conversation))


async def load_thread_source(database: AsyncSession, *, workspace_id: str, thread_id: str) -> ThreadSource:
    row = (
        await database.execute(
            select(ThreadRecord, RunRecord, SessionRecord)
            .select_from(ThreadRecord)
            .join(
                SessionRecord,
                and_(
                    SessionRecord.organization_id == ThreadRecord.organization_id,
                    SessionRecord.id == ThreadRecord.session_id,
                ),
            )
            .outerjoin(
                RunRecord,
                and_(
                    RunRecord.organization_id == ThreadRecord.organization_id,
                    RunRecord.thread_id == ThreadRecord.id,
                    RunRecord.id == ThreadRecord.current_run_id,
                ),
            )
            .where(ThreadRecord.id == thread_id, SessionRecord.workspace_id == workspace_id)
        )
    ).one_or_none()
    if row is None:
        raise command_not_found()
    thread, current, conversation = row
    return ThreadSource(
        thread.to_resource(),
        current.to_resource() if current is not None else None,
        SessionScope.from_record(conversation),
    )


async def load_thread_head(database: AsyncSession, source: ThreadSource) -> Run | None:
    thread, current = source.thread, source.current
    if thread.head_run_id is None:
        return None
    if current is not None and current.id == thread.head_run_id:
        return current
    head = await database.scalar(
        select(RunRecord).where(
            RunRecord.id == thread.head_run_id,
            RunRecord.organization_id == thread.organization_id,
            RunRecord.thread_id == thread.id,
        )
    )
    return head.to_resource() if head is not None else None
