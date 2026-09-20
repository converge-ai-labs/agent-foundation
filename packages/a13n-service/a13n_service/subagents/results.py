"""Idempotent child-result publication and safe Harness projection."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import anyio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.background import Sweep
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.iam.operation import authorization_operation
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
from a13n_service.interactions.session_scope import SessionScope
from a13n_service.run_stream import RunDisplayStore
from a13n_service.storage import ObjectStoreError, short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .domain import ChildRunRelationship
from .models import ChildRunRelationshipRecord
from .result_binding import select_result_binding
from .result_payload import (
    AsyncSubagentResultError,
    AsyncSubagentResultItemUnavailable,
    build_async_subagent_result_payload,
    load_async_subagent_terminal_item,
    parse_async_subagent_result_entry,
    read_child_result_source,
    validate_child_result_source,
)

logger = logging.getLogger("a13n_service.subagents.results")


@dataclass(frozen=True, slots=True)
class _PublicationAuthority:
    relationship: ChildRunRelationship
    parent_thread_id: str
    child: Run
    session_scope: SessionScope


class AsyncSubagentResultPublisher:
    """Allocate exactly one parent-inbox position for one sealed child Run."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        displays: RunDisplayStore,
        *,
        signals: ThreadControlSignalPublisher | None = None,
        max_pending_count: int = 256,
        max_pending_bytes: int = 8 * 1024 * 1024,
        entry_id_factory: Callable[[], str] = new_thread_inbox_entry_id,
        clock: Clock = utc_now,
    ) -> None:
        if max_pending_count < 1 or max_pending_bytes < 1:
            raise ValueError("Thread inbox admission limits must be positive")
        self._after_relationship_id = ""
        self._sessions = sessions
        self._displays = displays
        self._signals = signals
        self._max_pending_count = max_pending_count
        self._max_pending_bytes = max_pending_bytes
        self._entry_id_factory = entry_id_factory
        self._clock = clock

    @authorization_operation
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
            self._displays,
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
                result_row = (
                    await database.execute(
                        select(ChildRunRelationshipRecord, RunRecord)
                        .join(
                            RunRecord,
                            (RunRecord.organization_id == ChildRunRelationshipRecord.organization_id)
                            & (RunRecord.id == ChildRunRelationshipRecord.child_run_id),
                        )
                        .where(
                            ChildRunRelationshipRecord.organization_id == organization_id,
                            ChildRunRelationshipRecord.id == authority.relationship.id,
                            ChildRunRelationshipRecord.child_run_id == child_run_id,
                        )
                        .with_for_update(of=ChildRunRelationshipRecord)
                    )
                ).one_or_none()
                if thread is None or parent is None or result_row is None or parent.thread_id != thread.id:
                    raise AsyncSubagentResultError("child result relationship authority is incomplete")
                locked_relationship, child = result_row
                source = validate_child_result_source(
                    locked_relationship.to_resource(), child.to_resource(), parent.to_resource()
                )
                await _authorize_publication(
                    database,
                    parent=source.parent,
                    child=source.child,
                    relationship_id=authority.relationship.id,
                    session_scope=authority.session_scope,
                )
                payload = build_async_subagent_result_payload(
                    source.relationship,
                    source.child,
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
                        origin=parent,
                    )
                entry = await allocate_async_result(
                    database,
                    thread=thread,
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
            display = await self._read_existing(
                organization_id=organization_id,
                relationship_id=authority.relationship.id,
            )
            if display is None:
                raise ThreadInboxConflict("child result publication lost a concurrent mutation") from error
            expected = build_async_subagent_result_payload(
                authority.relationship,
                authority.child,
                terminal_item=terminal_item,
            )
            if parse_async_subagent_result_entry(display) != expected:
                raise AsyncSubagentResultError("concurrent child result does not match sealed authority") from error
            entry = display
        if created and entry.status is ThreadInboxStatus.pending:
            await self._best_effort_signal(organization_id=organization_id, thread_id=entry.thread_id)
        return entry

    async def reconcile_once(self, *, limit: int = 64) -> int:
        """Publish a bounded batch of sealed children that lack inbox evidence."""

        return (await self.scan(limit=limit)).completed

    async def scan(self, *, limit: int = 64, item_timeout_seconds: float = 30) -> Sweep:
        if limit < 1 or limit > 1024:
            raise ValueError("child result reconciliation limit is invalid")
        async with short_session(self._sessions) as database:
            candidates = tuple(
                (
                    await database.execute(
                        select(
                            ChildRunRelationshipRecord.organization_id,
                            ChildRunRelationshipRecord.child_run_id,
                            ChildRunRelationshipRecord.id,
                            RunRecord.sealed_at,
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
                            ChildRunRelationshipRecord.id > self._after_relationship_id,
                        )
                        .order_by(ChildRunRelationshipRecord.id)
                        .limit(limit)
                    )
                )
                .tuples()
                .all()
            )
        if not candidates:
            self._after_relationship_id = ""
            return Sweep()
        published = 0
        for organization_id, child_run_id, relationship_id, _ in candidates:
            self._after_relationship_id = relationship_id
            try:
                with anyio.fail_after(item_timeout_seconds):
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
            except (AsyncSubagentResultError, ThreadInboxConflict, ObjectStoreError, TimeoutError):
                logger.warning(
                    "async_subagent_result_recovery_deferred",
                    extra={"event": "async_subagent_result_recovery_deferred", "child_run_id": child_run_id},
                )
                continue
            published += 1
        return Sweep(
            examined=len(candidates),
            completed=published,
            deferred=len(candidates) - published,
            oldest_age_seconds=max(
                (assume_utc(self._clock()) - assume_utc(sealed)).total_seconds()
                for _, _, _, sealed in candidates
                if sealed is not None
            ),
        )

    async def _read_publication_authority(
        self,
        *,
        organization_id: str,
        child_run_id: str,
    ) -> _PublicationAuthority:
        async with short_session(self._sessions) as database:
            source = await read_child_result_source(
                database, organization_id=organization_id, child_run_id=child_run_id
            )
            session_scope = await _authorize_publication(
                database,
                parent=source.parent,
                child=source.child,
                relationship_id=source.relationship.id,
            )
            return _PublicationAuthority(
                relationship=source.relationship,
                parent_thread_id=source.parent.thread_id,
                child=source.child,
                session_scope=session_scope,
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
    parent: Run,
    child: Run,
    relationship_id: str,
    session_scope: SessionScope | None = None,
) -> SessionScope:
    if session_scope is None:
        session = await database.scalar(
            select(SessionRecord).where(
                SessionRecord.organization_id == parent.organization_id,
                SessionRecord.id == parent.session_id,
            )
        )
        session_scope = None if session is None else SessionScope.from_record(session)
    if (
        session_scope is None
        or parent.organization_id != session_scope.organization_id
        or parent.session_id != session_scope.id
        or child.organization_id != session_scope.organization_id
        or child.session_id != session_scope.id
    ):
        raise AsyncSubagentResultError("child result Session authority is incomplete")
    actor = AuthenticatedActor(
        principal=parent.authority_principal,
        auth_method="run_authority",
        credential_id=f"run_{parent.id}",
        boundary_workspace_id=session_scope.workspace_id,
        request_id=relationship_id,
    )
    try:
        for agent_id in (parent.agent_id, child.agent_id):
            await authorize_agent(
                database,
                actor=actor,
                workspace_id=session_scope.workspace_id,
                agent_id=agent_id,
                action=WorkspaceAction.run_read,
            )
    except AuthorizationError as error:
        raise AsyncSubagentResultError("child result publication is no longer authorized") from error
    return session_scope


async def _initial_binding(
    database: AsyncSession,
    *,
    thread: ThreadRecord,
    origin: RunRecord,
) -> tuple[str | None, str | None]:
    current = (
        origin
        if origin.id == thread.current_run_id
        else await database.scalar(
            select(RunRecord)
            .where(
                RunRecord.organization_id == thread.organization_id,
                RunRecord.thread_id == thread.id,
                RunRecord.id == thread.current_run_id,
            )
            .with_for_update()
        )
    )
    if current is None:
        raise AsyncSubagentResultError("parent Thread current Run was not found")
    head = None
    if current.status in {RunStatus.failed.value, RunStatus.cancelled.value} and thread.head_run_id is not None:
        head = (
            origin
            if origin.id == thread.head_run_id
            else await database.scalar(
                select(RunRecord)
                .where(
                    RunRecord.organization_id == thread.organization_id,
                    RunRecord.thread_id == thread.id,
                    RunRecord.id == thread.head_run_id,
                )
                .with_for_update()
            )
        )
        if head is None:
            raise AsyncSubagentResultError("parent Thread selected head was not found")
    return select_result_binding(
        thread.to_resource(), current.to_resource(), None if head is None else head.to_resource()
    )


__all__ = [
    "AsyncSubagentResultPublisher",
]
