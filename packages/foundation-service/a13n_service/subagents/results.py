"""Idempotent child-result publication and safe Harness projection."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.interactions.control_domain import (
    ThreadInboxEntry,
    ThreadInboxStatus,
    new_thread_inbox_entry_id,
)
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.domain import Run, RunStatus
from a13n_service.interactions.inbox import ThreadControlSignalPublisher
from a13n_service.interactions.inbox_allocation import allocate_async_result
from a13n_service.interactions.inbox_persistence import (
    ThreadInboxCapacityExceeded,
    ThreadInboxConflict,
)
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.run_stream import RunReplayStore
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .domain import ChildRunRelationship
from .models import ChildRunRelationshipRecord
from .result_payload import (
    AsyncSubagentResultError,
    AsyncSubagentResultItemUnavailable,
    build_async_subagent_result_payload,
    load_async_subagent_terminal_item,
    parse_async_subagent_result_entry,
)

logger = logging.getLogger("a13n_service.subagents.results")


@dataclass(frozen=True, slots=True)
class _PublicationAuthority:
    relationship: ChildRunRelationship
    parent_thread_id: str
    child: Run


class AsyncSubagentResultPublisher:
    """Allocate exactly one parent-inbox position for one sealed child Run."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        replays: RunReplayStore,
        *,
        signals: ThreadControlSignalPublisher | None = None,
        max_pending_count: int = 256,
        max_pending_bytes: int = 8 * 1024 * 1024,
        entry_id_factory: Callable[[], str] = new_thread_inbox_entry_id,
        clock: Clock = utc_now,
    ) -> None:
        if max_pending_count < 1 or max_pending_bytes < 1:
            raise ValueError("Thread inbox admission limits must be positive")
        self._sessions = sessions
        self._replays = replays
        self._signals = signals
        self._max_pending_count = max_pending_count
        self._max_pending_bytes = max_pending_bytes
        self._entry_id_factory = entry_id_factory
        self._clock = clock

    async def publish(
        self,
        *,
        organization_id: str,
        child_run_id: str,
    ) -> ThreadInboxEntry:
        authority = await self._read_publication_authority(
            organization_id=organization_id,
            child_run_id=child_run_id,
        )
        terminal_item = await load_async_subagent_terminal_item(
            self._replays,
            organization_id=organization_id,
            child=authority.child,
            expected_item_id=None,
        )
        now = assume_utc(self._clock())
        created = False
        try:
            async with transaction(self._sessions) as database:
                thread = await database.scalar(
                    select(ThreadRecord)
                    .where(
                        ThreadRecord.organization_id == organization_id, ThreadRecord.id == authority.parent_thread_id
                    )
                    .with_for_update()
                )
                parent = await database.scalar(
                    select(RunRecord)
                    .where(
                        RunRecord.organization_id == organization_id,
                        RunRecord.id == authority.relationship.parent_run_id,
                    )
                    .with_for_update()
                )
                locked_relationship = await database.scalar(
                    select(ChildRunRelationshipRecord)
                    .where(
                        ChildRunRelationshipRecord.organization_id == organization_id,
                        ChildRunRelationshipRecord.id == authority.relationship.id,
                    )
                    .with_for_update()
                )
                child = await database.scalar(
                    select(RunRecord).where(
                        RunRecord.organization_id == organization_id,
                        RunRecord.id == child_run_id,
                    )
                )
                if (
                    thread is None
                    or parent is None
                    or locked_relationship is None
                    or child is None
                    or parent.thread_id != thread.id
                    or locked_relationship.parent_run_id != parent.id
                    or locked_relationship.child_run_id != child.id
                ):
                    raise AsyncSubagentResultError("child result relationship authority is incomplete")
                await _authorize_publication(
                    database,
                    parent=parent,
                    child=child,
                    relationship_id=authority.relationship.id,
                )
                payload = build_async_subagent_result_payload(
                    locked_relationship.to_resource(),
                    child.to_resource(),
                    terminal_item=terminal_item,
                )
                existing = await _load_inbox_entry(
                    database,
                    organization_id=organization_id,
                    relationship_id=authority.relationship.id,
                )
                if existing is not None:
                    resource = existing.to_resource()
                    if parse_async_subagent_result_entry(resource) != payload:
                        raise AsyncSubagentResultError("persisted child result does not match sealed authority")
                    return resource
                suppressed = parent.status in {RunStatus.failed.value, RunStatus.cancelled.value}
                if suppressed:
                    target_run_id, source_waiting_run_id = None, None
                else:
                    target_run_id, source_waiting_run_id = await _initial_binding(
                        database,
                        thread=thread,
                    )
                entry = await allocate_async_result(
                    database,
                    organization_id=organization_id,
                    thread_id=thread.id,
                    origin_run_id=parent.id,
                    relationship_id=authority.relationship.id,
                    target_run_id=target_run_id,
                    source_waiting_run_id=source_waiting_run_id,
                    entry_id=self._entry_id_factory(),
                    payload=payload.as_json(),
                    payload_size_bytes=len(payload.canonical_bytes()),
                    suppressed=suppressed,
                    max_pending_count=self._max_pending_count,
                    max_pending_bytes=self._max_pending_bytes,
                    now=now,
                )
                created = True
        except IntegrityError as error:
            replay = await self._read_existing(
                organization_id=organization_id,
                relationship_id=authority.relationship.id,
            )
            if replay is None:
                raise ThreadInboxConflict("child result publication lost a concurrent mutation") from error
            expected = build_async_subagent_result_payload(
                authority.relationship,
                authority.child,
                terminal_item=terminal_item,
            )
            if parse_async_subagent_result_entry(replay) != expected:
                raise AsyncSubagentResultError("concurrent child result does not match sealed authority") from error
            entry = replay
        if created and entry.status is ThreadInboxStatus.pending:
            await self._best_effort_signal(organization_id=organization_id, thread_id=entry.thread_id)
        return entry

    async def reconcile_once(self, *, limit: int = 64) -> int:
        """Publish a bounded batch of sealed children that lack inbox evidence."""

        if limit < 1 or limit > 1024:
            raise ValueError("child result reconciliation limit is invalid")
        async with short_session(self._sessions) as database:
            candidates = tuple(
                (
                    await database.execute(
                        select(
                            ChildRunRelationshipRecord.organization_id,
                            ChildRunRelationshipRecord.child_run_id,
                        )
                        .join(
                            RunRecord,
                            (RunRecord.organization_id == ChildRunRelationshipRecord.organization_id)
                            & (RunRecord.id == ChildRunRelationshipRecord.child_run_id),
                        )
                        .outerjoin(
                            ThreadInboxRecord,
                            (ThreadInboxRecord.organization_id == ChildRunRelationshipRecord.organization_id)
                            & (ThreadInboxRecord.async_subagent_relationship_id == ChildRunRelationshipRecord.id),
                        )
                        .where(
                            RunRecord.status.in_(("completed", "failed", "cancelled")),
                            ThreadInboxRecord.id.is_(None),
                        )
                        .order_by(RunRecord.sealed_at, ChildRunRelationshipRecord.id)
                        .limit(limit)
                    )
                )
                .tuples()
                .all()
            )
        published = 0
        for organization_id, child_run_id in candidates:
            try:
                await self.publish(organization_id=organization_id, child_run_id=child_run_id)
            except ThreadInboxCapacityExceeded:
                logger.info(
                    "async_subagent_result_capacity_deferred",
                    extra={"event": "async_subagent_result_capacity_deferred", "child_run_id": child_run_id},
                )
                continue
            except AsyncSubagentResultItemUnavailable:
                logger.info(
                    "async_subagent_result_item_deferred",
                    extra={"event": "async_subagent_result_item_deferred", "child_run_id": child_run_id},
                )
                continue
            published += 1
        return published

    async def _read_publication_authority(
        self,
        *,
        organization_id: str,
        child_run_id: str,
    ) -> _PublicationAuthority:
        async with short_session(self._sessions) as database:
            relationship = await database.scalar(
                select(ChildRunRelationshipRecord).where(
                    ChildRunRelationshipRecord.organization_id == organization_id,
                    ChildRunRelationshipRecord.child_run_id == child_run_id,
                )
            )
            if relationship is None:
                raise AsyncSubagentResultError("child Run relationship was not found")
            parent = await database.scalar(
                select(RunRecord).where(
                    RunRecord.organization_id == organization_id,
                    RunRecord.id == relationship.parent_run_id,
                )
            )
            child = await database.scalar(
                select(RunRecord).where(
                    RunRecord.organization_id == organization_id,
                    RunRecord.id == child_run_id,
                )
            )
            if (
                parent is None
                or child is None
                or relationship.child_run_id != child.id
                or relationship.child_thread_id != child.thread_id
            ):
                raise AsyncSubagentResultError("child result relationship authority is incomplete")
            await _authorize_publication(
                database,
                parent=parent,
                child=child,
                relationship_id=relationship.id,
            )
            return _PublicationAuthority(
                relationship=relationship.to_resource(),
                parent_thread_id=parent.thread_id,
                child=child.to_resource(),
            )

    async def _read_existing(self, *, organization_id: str, relationship_id: str) -> ThreadInboxEntry | None:
        async with short_session(self._sessions) as database:
            record = await _load_inbox_entry(database, organization_id=organization_id, relationship_id=relationship_id)
            return None if record is None else record.to_resource()

    async def _best_effort_signal(self, *, organization_id: str, thread_id: str) -> None:
        if self._signals is None:
            return
        try:
            await self._signals.publish(organization_id=organization_id, thread_id=thread_id)
        except Exception:
            logger.warning(
                "async_subagent_result_signal_failed",
                extra={"event": "async_subagent_result_signal_failed", "thread_id": thread_id},
                exc_info=True,
            )


async def _load_inbox_entry(
    database: AsyncSession,
    *,
    organization_id: str,
    relationship_id: str,
) -> ThreadInboxRecord | None:
    return await database.scalar(
        select(ThreadInboxRecord).where(
            ThreadInboxRecord.organization_id == organization_id,
            ThreadInboxRecord.async_subagent_relationship_id == relationship_id,
        )
    )


async def _authorize_publication(
    database: AsyncSession,
    *,
    parent: RunRecord,
    child: RunRecord,
    relationship_id: str,
) -> None:
    session = await database.scalar(
        select(SessionRecord).where(
            SessionRecord.organization_id == parent.organization_id,
            SessionRecord.id == parent.session_id,
        )
    )
    if session is None or child.session_id != session.id:
        raise AsyncSubagentResultError("child result Session authority is incomplete")
    actor = AuthenticatedActor(
        principal=parent.to_resource().authority_principal,
        auth_method="run_authority",
        credential_id=f"run_{parent.id}",
        boundary_workspace_id=session.workspace_id,
        request_id=relationship_id,
    )
    try:
        for agent_id in (parent.agent_id, child.agent_id):
            await authorize_agent(
                database,
                actor=actor,
                workspace_id=session.workspace_id,
                agent_id=agent_id,
                action=WorkspaceAction.run_read,
            )
    except AuthorizationError as error:
        raise AsyncSubagentResultError("child result publication is no longer authorized") from error


async def _initial_binding(
    database: AsyncSession,
    *,
    thread: ThreadRecord,
) -> tuple[str | None, str | None]:
    current = await database.scalar(
        select(RunRecord)
        .where(RunRecord.organization_id == thread.organization_id, RunRecord.id == thread.current_run_id)
        .with_for_update()
    )
    if current is None:
        raise AsyncSubagentResultError("parent Thread current Run was not found")
    if current.status in {RunStatus.accepted.value, RunStatus.running.value}:
        return current.id, None
    if current.status == RunStatus.waiting.value and thread.head_run_id == current.id:
        return None, current.id
    if current.status in {RunStatus.failed.value, RunStatus.cancelled.value} and thread.head_run_id is not None:
        head = await database.scalar(
            select(RunRecord)
            .where(RunRecord.organization_id == thread.organization_id, RunRecord.id == thread.head_run_id)
            .with_for_update()
        )
        if head is None:
            raise AsyncSubagentResultError("parent Thread selected head was not found")
        if head.status == RunStatus.waiting.value:
            return None, head.id
    return None, None


__all__ = [
    "AsyncSubagentResultPublisher",
]
