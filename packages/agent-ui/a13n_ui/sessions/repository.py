"""Short-transaction persistence for Sessions, Threads, Turns, and checkpoints."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import JsonValue, ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from a13n_ui.composition import ResolvedEnvironmentSnapshot, SnapshotReference
from a13n_ui.configuration import ResourceKind, ResourceRevisionRef
from a13n_ui.errors import SessionError, StoreIntegrityError
from a13n_ui.storage.database import short_session, transaction
from a13n_ui.storage.models import (
    CompositionSnapshotRecord,
    PendingDeferredRecord,
    SessionPresentationRecord,
    SessionRecord,
    SessionThreadRecord,
    ThreadCheckpointRecord,
    TurnRecord,
    TurnRunRecord,
)
from a13n_ui.storage.objects import ObjectRef
from a13n_ui.storage.runtime import LocalStore

from .models import (
    CheckpointRef,
    LocalSession,
    PendingDeferredRef,
    SessionAgentSkillSelection,
    SessionForkRef,
    SessionLifecycleState,
    SessionSummary,
    SessionUpdate,
    ThreadView,
    TurnState,
    TurnView,
    WaitingReason,
)

_TERMINAL_TURN_STATES = {
    TurnState.completed.value,
    TurnState.failed.value,
    TurnState.cancelled.value,
    TurnState.interrupted.value,
}


class SessionRepository:
    """Own authoritative SQLite transitions without spanning external I/O."""

    def __init__(self, store: LocalStore) -> None:
        self._store = store

    async def create(
        self,
        *,
        session_id: str,
        creation_request_id: str,
        thread_id: str,
        agent_snapshot: SnapshotReference,
        environment_snapshot: SnapshotReference,
        resolved_environment: ResolvedEnvironmentSnapshot,
        title: str | None,
        skill_selections: Sequence[SessionAgentSkillSelection],
        baseline_checkpoint: CheckpointRef,
        baseline_owner_kind: Literal["session_baseline", "session_fork"],
        parent_fork: SessionForkRef | None = None,
    ) -> LocalSession:
        now = datetime.now(UTC)
        skills_json = _json([item.model_dump(mode="json") for item in skill_selections])
        fork_json = _json(parent_fork.model_dump(mode="json")) if parent_fork is not None else None
        async with transaction(self._store.database.sessions) as database_session:
            existing = (
                await database_session.execute(
                    select(SessionRecord).where(SessionRecord.creation_request_id == creation_request_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                if (
                    existing.agent_snapshot_id != await self._snapshot_id(database_session, agent_snapshot, "agent")
                    or existing.environment_snapshot_id
                    != await self._snapshot_id(database_session, environment_snapshot, "environment")
                    or existing.skill_selections_json != skills_json
                    or existing.parent_fork_json != fork_json
                    or existing.title != title
                ):
                    raise SessionError(
                        "The Session creation request was already used with different content.",
                        code="session_creation_conflict",
                    )
                existing_id = existing.session_id
            else:
                agent_snapshot_id = await self._snapshot_id(database_session, agent_snapshot, "agent")
                environment_snapshot_id = await self._snapshot_id(
                    database_session,
                    environment_snapshot,
                    "environment",
                )
                record = SessionRecord(
                    session_id=session_id,
                    creation_request_id=creation_request_id,
                    root_thread_id=None,
                    lifecycle_state=SessionLifecycleState.provisioning.value,
                    lifecycle_failure_json=None,
                    created_at=now,
                    updated_at=now,
                    title=title,
                    archived_at=None,
                    pinned=False,
                    display_order=0,
                    control_revision=1,
                    agent_snapshot_id=agent_snapshot_id,
                    environment_snapshot_id=environment_snapshot_id,
                    skill_selections_json=skills_json,
                    parent_fork_json=fork_json,
                )
                database_session.add(record)
                await database_session.flush()
                if baseline_checkpoint.thread_id != thread_id:
                    raise SessionError(
                        "The Session baseline checkpoint belongs to another Thread.",
                        code="checkpoint_thread_mismatch",
                    )
                database_session.add(
                    SessionThreadRecord(
                        thread_id=thread_id,
                        session_id=session_id,
                        root_ordinal=0,
                        commit_revision=0,
                        queue_revision=0,
                        selected_checkpoint_id=None,
                        active_turn_id=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                database_session.add(
                    SessionPresentationRecord(
                        session_id=session_id,
                        next_sequence=1,
                        retention_generation=1,
                        projection_watermark=0,
                        last_segment_digest=None,
                    )
                )
                from a13n_ui.environments.repository import seed_environment_assignments

                await seed_environment_assignments(
                    database_session,
                    session_id=session_id,
                    snapshot=resolved_environment,
                    created_at=now,
                )
                await database_session.flush()
                database_session.add(
                    ThreadCheckpointRecord(
                        checkpoint_id=baseline_checkpoint.checkpoint_id,
                        thread_id=thread_id,
                        owner_kind=baseline_owner_kind,
                        owner_turn_id=None,
                        state_object_digest=baseline_checkpoint.state_object_digest,
                        harness_release=baseline_checkpoint.harness_release,
                        created_at=now,
                    )
                )
                await database_session.flush()
                record.root_thread_id = thread_id
                thread_record = await database_session.get(SessionThreadRecord, thread_id)
                assert thread_record is not None
                thread_record.selected_checkpoint_id = baseline_checkpoint.checkpoint_id
                existing_id = session_id
        return await self.get(existing_id)

    async def get(self, session_id: str) -> LocalSession:
        async with short_session(self._store.database.sessions) as database_session:
            session_row = await database_session.get(SessionRecord, session_id)
            if session_row is None or session_row.lifecycle_state == SessionLifecycleState.deleted.value:
                raise SessionError("The selected Session does not exist.", code="session_missing")
            if session_row.root_thread_id is None:
                raise StoreIntegrityError(
                    "A retained Session has no root Thread.",
                    code="session_root_missing",
                )
            thread_row = await database_session.get(SessionThreadRecord, session_row.root_thread_id)
            if thread_row is None or thread_row.session_id != session_id:
                raise StoreIntegrityError(
                    "A retained Session root Thread is invalid.",
                    code="session_root_invalid",
                )
            agent_row = await database_session.get(CompositionSnapshotRecord, session_row.agent_snapshot_id)
            environment_row = await database_session.get(
                CompositionSnapshotRecord,
                session_row.environment_snapshot_id,
            )
            turns = tuple(
                (
                    await database_session.execute(
                        select(TurnRecord)
                        .where(TurnRecord.thread_id == thread_row.thread_id)
                        .order_by(TurnRecord.accepted_at, TurnRecord.turn_id)
                    )
                ).scalars()
            )
            checkpoint_ids = {
                checkpoint_id
                for checkpoint_id in (
                    thread_row.selected_checkpoint_id,
                    *(turn.base_checkpoint_id for turn in turns),
                    *(turn.selected_checkpoint_id for turn in turns),
                )
                if checkpoint_id is not None
            }
            checkpoints = {
                row.checkpoint_id: row
                for row in (
                    (
                        await database_session.execute(
                            select(ThreadCheckpointRecord).where(
                                ThreadCheckpointRecord.checkpoint_id.in_(checkpoint_ids)
                            )
                        )
                    ).scalars()
                    if checkpoint_ids
                    else ()
                )
            }
            turn_ids = tuple(turn.turn_id for turn in turns)
            run_rows = tuple(
                (
                    await database_session.execute(
                        select(TurnRunRecord)
                        .where(TurnRunRecord.turn_id.in_(turn_ids))
                        .order_by(TurnRunRecord.turn_id, TurnRunRecord.ordinal)
                    )
                ).scalars()
                if turn_ids
                else ()
            )
            deferred_rows = {
                row.turn_id: row
                for row in (
                    (
                        await database_session.execute(
                            select(PendingDeferredRecord).where(PendingDeferredRecord.turn_id.in_(turn_ids))
                        )
                    ).scalars()
                    if turn_ids
                    else ()
                )
            }
        if agent_row is None or environment_row is None:
            raise StoreIntegrityError(
                "A Session references a missing composition snapshot.",
                code="session_snapshot_missing",
            )
        runs: dict[str, list[str]] = {}
        for row in run_rows:
            runs.setdefault(row.turn_id, []).append(row.run_id)
        checkpoint_refs = {key: _checkpoint_ref(value) for key, value in checkpoints.items()}
        turn_views = tuple(
            _turn_view(
                row,
                checkpoints=checkpoint_refs,
                run_ids=tuple(runs.get(row.turn_id, ())),
                deferred=deferred_rows.get(row.turn_id),
            )
            for row in turns
        )
        try:
            return LocalSession(
                session_id=session_row.session_id,
                creation_request_id=session_row.creation_request_id,
                lifecycle_state=SessionLifecycleState(session_row.lifecycle_state),
                lifecycle_failure=_load_json(session_row.lifecycle_failure_json),
                created_at=session_row.created_at,
                updated_at=session_row.updated_at,
                title=session_row.title,
                archived_at=session_row.archived_at,
                pinned=session_row.pinned,
                display_order=session_row.display_order,
                control_revision=session_row.control_revision,
                agent_snapshot=_snapshot_reference(agent_row),
                environment_snapshot=_snapshot_reference(environment_row),
                skill_selections=tuple(
                    SessionAgentSkillSelection.model_validate(item, strict=True)
                    for item in _require_list(_load_json(session_row.skill_selections_json))
                ),
                parent_fork=(
                    SessionForkRef.model_validate(_load_json(session_row.parent_fork_json), strict=True)
                    if session_row.parent_fork_json is not None
                    else None
                ),
                root=ThreadView(
                    thread_id=thread_row.thread_id,
                    session_id=session_row.session_id,
                    commit_revision=thread_row.commit_revision,
                    queue_revision=thread_row.queue_revision,
                    selected_checkpoint=(
                        checkpoint_refs.get(thread_row.selected_checkpoint_id)
                        if thread_row.selected_checkpoint_id is not None
                        else None
                    ),
                    active_turn_id=thread_row.active_turn_id,
                    turns=turn_views,
                ),
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise StoreIntegrityError(
                "A retained Session projection is invalid.",
                code="session_projection_invalid",
            ) from exc

    async def recovery_session_ids(self) -> tuple[str, ...]:
        """Return every retained Session identity for startup authority validation."""

        async with short_session(self._store.database.sessions) as database_session:
            return tuple(
                (
                    await database_session.execute(
                        select(SessionRecord.session_id)
                        .where(SessionRecord.lifecycle_state != SessionLifecycleState.deleted.value)
                        .order_by(SessionRecord.session_id)
                    )
                ).scalars()
            )

    async def fail_closed(self, session_id: str, *, failure: JsonValue) -> None:
        """Block one retained Session whose selected startup authority is invalid."""

        async with transaction(self._store.database.sessions) as database_session:
            record = await database_session.get(SessionRecord, session_id)
            if record is None or record.lifecycle_state == SessionLifecycleState.deleted.value:
                return
            record.lifecycle_state = SessionLifecycleState.blocked.value
            record.lifecycle_failure_json = _json(failure)
            record.control_revision += 1
            record.updated_at = datetime.now(UTC)

    async def list(
        self,
        *,
        include_archived: bool = False,
        limit: int = 100,
    ) -> tuple[SessionSummary, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("Session list limit must be between 1 and 1000")
        async with short_session(self._store.database.sessions) as database_session:
            statement = (
                select(SessionRecord, SessionThreadRecord)
                .join(SessionThreadRecord, SessionRecord.root_thread_id == SessionThreadRecord.thread_id)
                .where(SessionRecord.lifecycle_state != SessionLifecycleState.deleted.value)
            )
            if not include_archived:
                statement = statement.where(SessionRecord.archived_at.is_(None))
            rows = tuple(
                (
                    await database_session.execute(
                        statement.order_by(
                            SessionRecord.pinned.desc(),
                            SessionRecord.display_order,
                            SessionRecord.updated_at.desc(),
                        ).limit(limit)
                    )
                ).tuples()
            )
        try:
            return tuple(
                SessionSummary(
                    session_id=session_row.session_id,
                    lifecycle_state=SessionLifecycleState(session_row.lifecycle_state),
                    title=session_row.title,
                    archived_at=session_row.archived_at,
                    pinned=session_row.pinned,
                    display_order=session_row.display_order,
                    control_revision=session_row.control_revision,
                    thread_id=thread_row.thread_id,
                    thread_commit_revision=thread_row.commit_revision,
                    active_turn_id=thread_row.active_turn_id,
                    updated_at=session_row.updated_at,
                )
                for session_row, thread_row in rows
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise StoreIntegrityError(
                "A retained Session summary is invalid.",
                code="session_projection_invalid",
            ) from exc

    async def update(self, session_id: str, expected_revision: int, update: SessionUpdate) -> LocalSession:
        now = datetime.now(UTC)
        async with transaction(self._store.database.sessions) as database_session:
            record = await database_session.get(SessionRecord, session_id)
            self._require_control_revision(record, expected_revision)
            assert record is not None
            if record.lifecycle_state in {
                SessionLifecycleState.deleting.value,
                SessionLifecycleState.cleanup_pending.value,
                SessionLifecycleState.deleted.value,
            }:
                raise SessionError(
                    "A deleting Session cannot be edited.",
                    code="session_not_editable",
                )
            updates = update.model_fields_set
            if "title" in updates:
                record.title = update.title
            if "archived" in updates and update.archived is not None:
                record.archived_at = now if update.archived else None
            if "pinned" in updates and update.pinned is not None:
                record.pinned = update.pinned
            if "display_order" in updates and update.display_order is not None:
                record.display_order = update.display_order
            record.control_revision += 1
            record.updated_at = now
        return await self.get(session_id)

    async def set_lifecycle(
        self,
        session_id: str,
        *,
        expected_revision: int,
        state: SessionLifecycleState,
        failure: JsonValue | None = None,
    ) -> LocalSession:
        now = datetime.now(UTC)
        async with transaction(self._store.database.sessions) as database_session:
            record = await database_session.get(SessionRecord, session_id)
            self._require_control_revision(record, expected_revision)
            assert record is not None
            if state is SessionLifecycleState.deleting:
                thread = await database_session.get(SessionThreadRecord, record.root_thread_id)
                if thread is not None and thread.active_turn_id is not None:
                    raise SessionError(
                        "A Session with active work cannot be deleted.",
                        code="session_work_active",
                    )
            record.lifecycle_state = state.value
            record.lifecycle_failure_json = _json(failure) if failure is not None else None
            record.control_revision += 1
            record.updated_at = now
        return await self.get(session_id)

    async def accept_turn(
        self,
        *,
        session_id: str,
        thread_id: str,
        expected_revision: int,
        turn_id: str,
        input_value: JsonValue,
    ) -> TurnView:
        now = datetime.now(UTC)
        async with transaction(self._store.database.sessions) as database_session:
            session_row = await database_session.get(SessionRecord, session_id)
            if session_row is None or session_row.lifecycle_state != SessionLifecycleState.ready.value:
                raise SessionError(
                    "The selected Session cannot accept a Turn.",
                    code="session_not_ready",
                )
            thread_row = await database_session.get(SessionThreadRecord, thread_id)
            self._require_thread(thread_row, session_id, expected_revision)
            assert thread_row is not None
            if thread_row.active_turn_id is not None:
                raise SessionError(
                    "The Thread already has an advancing Turn.",
                    code="thread_turn_active",
                )
            turn = TurnRecord(
                turn_id=turn_id,
                session_id=session_id,
                thread_id=thread_id,
                input_json=_json(input_value),
                state=TurnState.accepted.value,
                waiting_reason=None,
                base_checkpoint_id=thread_row.selected_checkpoint_id,
                selected_checkpoint_id=None,
                terminal_projection_json=None,
                failure_json=None,
                process_generation=self._store.process_generation,
                accepted_at=now,
                started_at=None,
                finished_at=None,
            )
            database_session.add(turn)
            await database_session.flush()
            thread_row.active_turn_id = turn_id
            thread_row.commit_revision += 1
            thread_row.updated_at = now
        return (await self.get(session_id)).root.turns[-1]

    async def start_run(
        self,
        *,
        turn_id: str,
        expected_thread_revision: int,
        run_id: str,
        consume_deferred: bool,
    ) -> int:
        now = datetime.now(UTC)
        async with transaction(self._store.database.sessions) as database_session:
            turn = await database_session.get(TurnRecord, turn_id)
            if turn is None:
                raise SessionError("The selected Turn does not exist.", code="turn_missing")
            thread = await database_session.get(SessionThreadRecord, turn.thread_id)
            self._require_thread(thread, turn.session_id, expected_thread_revision)
            assert thread is not None
            if thread.active_turn_id != turn_id or turn.state not in {
                TurnState.accepted.value,
                TurnState.waiting.value,
            }:
                raise SessionError("The selected Turn cannot start a Run.", code="turn_not_runnable")
            deferred = await database_session.get(PendingDeferredRecord, turn_id)
            if consume_deferred:
                if turn.state != TurnState.waiting.value or deferred is None or deferred.consumed_by_run_id is not None:
                    raise SessionError(
                        "The selected deferred request is unavailable or already consumed.",
                        code="deferred_request_conflict",
                    )
                deferred.consumed_by_run_id = run_id
            elif turn.state == TurnState.waiting.value:
                raise SessionError(
                    "A waiting Turn requires exact deferred consumption.",
                    code="deferred_request_required",
                )
            ordinal = (
                int(
                    (
                        await database_session.execute(
                            select(TurnRunRecord.ordinal)
                            .where(TurnRunRecord.turn_id == turn_id)
                            .order_by(TurnRunRecord.ordinal.desc())
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    or 0
                )
                + 1
            )
            database_session.add(
                TurnRunRecord(
                    turn_id=turn_id,
                    ordinal=ordinal,
                    run_id=run_id,
                    accepted_at=now,
                )
            )
            turn.state = TurnState.running.value
            turn.waiting_reason = None
            turn.started_at = turn.started_at or now
            turn.process_generation = self._store.process_generation
            if consume_deferred:
                thread.commit_revision += 1
                thread.updated_at = now
            return thread.commit_revision

    async def commit_waiting(
        self,
        *,
        turn_id: str,
        expected_thread_revision: int,
        run_id: str,
        checkpoint: CheckpointRef,
        state_object: ObjectRef,
        deferred: PendingDeferredRef,
        waiting_reason: WaitingReason,
    ) -> TurnView:
        if checkpoint.state_object_digest != state_object.logical_digest:
            raise ValueError("checkpoint and state object digest must agree")
        now = datetime.now(UTC)
        async with transaction(self._store.database.sessions) as database_session:
            turn, thread = await self._require_running_turn(
                database_session,
                turn_id,
                expected_thread_revision,
                run_id,
            )
            await self._insert_checkpoint(database_session, turn, checkpoint, now)
            prior = await database_session.get(PendingDeferredRecord, turn_id)
            if prior is not None:
                await database_session.delete(prior)
                await database_session.flush()
            database_session.add(
                PendingDeferredRecord(
                    turn_id=turn_id,
                    object_digest=deferred.object_digest,
                    request_digest=deferred.request_digest,
                    request_codec_version=deferred.request_codec_version,
                    source_run_id=deferred.source_run_id,
                    consumed_by_run_id=None,
                )
            )
            turn.state = TurnState.waiting.value
            turn.waiting_reason = waiting_reason.value
            turn.selected_checkpoint_id = checkpoint.checkpoint_id
            turn.failure_json = None
            turn.terminal_projection_json = None
            thread.selected_checkpoint_id = checkpoint.checkpoint_id
            thread.commit_revision += 1
            thread.updated_at = now
        session = await self.get(turn.session_id)
        return next(item for item in session.root.turns if item.turn_id == turn_id)

    async def commit_terminal(
        self,
        *,
        turn_id: str,
        expected_thread_revision: int,
        run_id: str,
        state: Literal[
            TurnState.completed,
            TurnState.failed,
            TurnState.cancelled,
            TurnState.interrupted,
        ],
        checkpoint: CheckpointRef | None,
        terminal_projection: JsonValue | None,
        failure: JsonValue | None,
    ) -> TurnView:
        now = datetime.now(UTC)
        async with transaction(self._store.database.sessions) as database_session:
            turn, thread = await self._require_running_turn(
                database_session,
                turn_id,
                expected_thread_revision,
                run_id,
            )
            if checkpoint is not None:
                await self._insert_checkpoint(database_session, turn, checkpoint, now)
                turn.selected_checkpoint_id = checkpoint.checkpoint_id
                thread.selected_checkpoint_id = checkpoint.checkpoint_id
            turn.state = state.value
            turn.waiting_reason = None
            turn.terminal_projection_json = _json(terminal_projection) if terminal_projection is not None else None
            turn.failure_json = _json(failure) if failure is not None else None
            turn.finished_at = now
            thread.active_turn_id = None
            thread.commit_revision += 1
            thread.updated_at = now
            deferred = await database_session.get(PendingDeferredRecord, turn_id)
            if deferred is not None:
                await database_session.delete(deferred)
            session_row = await database_session.get(SessionRecord, turn.session_id)
            if session_row is not None:
                session_row.updated_at = now
        session = await self.get(turn.session_id)
        return next(item for item in session.root.turns if item.turn_id == turn_id)

    async def commit_unstarted_terminal(
        self,
        *,
        turn_id: str,
        expected_thread_revision: int,
        state: Literal[TurnState.cancelled, TurnState.interrupted],
        failure: JsonValue | None,
    ) -> TurnView:
        """Close accepted or waiting work that never entered another Harness Run."""

        now = datetime.now(UTC)
        async with transaction(self._store.database.sessions) as database_session:
            turn = await database_session.get(TurnRecord, turn_id)
            if turn is None:
                raise SessionError("The selected Turn does not exist.", code="turn_missing")
            thread = await database_session.get(SessionThreadRecord, turn.thread_id)
            self._require_thread(thread, turn.session_id, expected_thread_revision)
            assert thread is not None
            if thread.active_turn_id != turn_id or turn.state not in {
                TurnState.accepted.value,
                TurnState.waiting.value,
            }:
                raise SessionError("The selected Turn cannot be closed before a Run.", code="turn_not_cancellable")
            turn.state = state.value
            turn.waiting_reason = None
            turn.failure_json = _json(failure) if failure is not None else None
            turn.terminal_projection_json = None
            turn.finished_at = now
            thread.active_turn_id = None
            thread.commit_revision += 1
            thread.updated_at = now
            deferred = await database_session.get(PendingDeferredRecord, turn_id)
            if deferred is not None:
                await database_session.delete(deferred)
            session_row = await database_session.get(SessionRecord, turn.session_id)
            if session_row is not None:
                session_row.updated_at = now
        session = await self.get(turn.session_id)
        return next(item for item in session.root.turns if item.turn_id == turn_id)

    async def interrupt_prior_process_turns(self) -> int:
        now = datetime.now(UTC)
        interrupted = 0
        async with transaction(self._store.database.sessions) as database_session:
            rows = tuple(
                (
                    await database_session.execute(
                        select(TurnRecord).where(
                            TurnRecord.state.in_((TurnState.accepted.value, TurnState.running.value)),
                            TurnRecord.process_generation != self._store.process_generation,
                        )
                    )
                ).scalars()
            )
            for turn in rows:
                turn.state = TurnState.interrupted.value
                turn.waiting_reason = None
                turn.failure_json = _json(
                    {
                        "code": "run_interrupted",
                        "message": "The prior Agent UI process ended before a durable Run boundary.",
                    }
                )
                turn.finished_at = now
                thread = await database_session.get(SessionThreadRecord, turn.thread_id)
                if thread is not None and thread.active_turn_id == turn.turn_id:
                    thread.active_turn_id = None
                    thread.commit_revision += 1
                    thread.updated_at = now
                interrupted += 1
        return interrupted

    async def hard_delete(self, session_id: str, expected_revision: int) -> None:
        async with transaction(self._store.database.sessions) as database_session:
            record = await database_session.get(SessionRecord, session_id)
            self._require_control_revision(record, expected_revision)
            assert record is not None
            thread = await database_session.get(SessionThreadRecord, record.root_thread_id)
            if thread is not None and thread.active_turn_id is not None:
                raise SessionError(
                    "A Session with active work cannot be deleted.",
                    code="session_work_active",
                )
            if thread is not None:
                thread.selected_checkpoint_id = None
                await database_session.execute(
                    update(TurnRecord)
                    .where(TurnRecord.thread_id == thread.thread_id)
                    .values(base_checkpoint_id=None, selected_checkpoint_id=None)
                )
                await database_session.flush()
                await database_session.execute(
                    delete(ThreadCheckpointRecord).where(
                        ThreadCheckpointRecord.thread_id == thread.thread_id,
                        ThreadCheckpointRecord.owner_turn_id.is_(None),
                    )
                )
            await database_session.execute(delete(SessionRecord).where(SessionRecord.session_id == session_id))

    async def _snapshot_id(
        self,
        database_session: object,
        reference: SnapshotReference,
        kind: Literal["agent", "environment"],
    ) -> int:
        # AsyncSession is kept out of the public type surface, but SQLAlchemy's
        # execute contract is used only inside this short transaction.
        from sqlalchemy.ext.asyncio import AsyncSession

        if not isinstance(database_session, AsyncSession):
            raise TypeError("database_session must be an AsyncSession")
        if reference.snapshot_kind != kind:
            raise SessionError("A Session snapshot selector has the wrong kind.", code="session_snapshot_invalid")
        row = (
            await database_session.execute(
                select(CompositionSnapshotRecord).where(
                    CompositionSnapshotRecord.snapshot_kind == kind,
                    CompositionSnapshotRecord.logical_digest == reference.logical_digest,
                    CompositionSnapshotRecord.object_digest == reference.object_digest,
                )
            )
        ).scalar_one_or_none()
        if row is None or _snapshot_reference(row) != reference:
            raise SessionError(
                "A selected Session composition snapshot is not retained.",
                code="session_snapshot_missing",
            )
        return row.snapshot_id

    def _require_control_revision(self, record: SessionRecord | None, expected_revision: int) -> None:
        if record is None or record.lifecycle_state == SessionLifecycleState.deleted.value:
            raise SessionError("The selected Session does not exist.", code="session_missing")
        if record.control_revision != expected_revision:
            raise SessionError(
                "The Session control revision is stale.",
                code="session_revision_conflict",
                details={"current_revision": record.control_revision},
            )

    def _require_thread(
        self,
        thread: SessionThreadRecord | None,
        session_id: str,
        expected_revision: int,
    ) -> None:
        if thread is None or thread.session_id != session_id:
            raise SessionError("The selected Thread does not exist in this Session.", code="thread_missing")
        if thread.commit_revision != expected_revision:
            raise SessionError(
                "The Thread commit revision is stale.",
                code="thread_revision_conflict",
                details={"current_revision": thread.commit_revision},
            )

    async def _require_running_turn(
        self,
        database_session: object,
        turn_id: str,
        expected_thread_revision: int,
        run_id: str,
    ) -> tuple[TurnRecord, SessionThreadRecord]:
        from sqlalchemy.ext.asyncio import AsyncSession

        if not isinstance(database_session, AsyncSession):
            raise TypeError("database_session must be an AsyncSession")
        turn = await database_session.get(TurnRecord, turn_id)
        if turn is None:
            raise SessionError("The selected Turn does not exist.", code="turn_missing")
        thread = await database_session.get(SessionThreadRecord, turn.thread_id)
        self._require_thread(thread, turn.session_id, expected_thread_revision)
        assert thread is not None
        if thread.active_turn_id != turn_id or turn.state != TurnState.running.value:
            raise SessionError("The selected Turn is not running.", code="turn_not_running")
        latest_run = (
            await database_session.execute(
                select(TurnRunRecord)
                .where(TurnRunRecord.turn_id == turn_id)
                .order_by(TurnRunRecord.ordinal.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest_run is None or latest_run.run_id != run_id:
            raise SessionError("The Run completion is stale.", code="run_revision_conflict")
        return turn, thread

    async def _insert_checkpoint(
        self,
        database_session: object,
        turn: TurnRecord,
        checkpoint: CheckpointRef,
        created_at: datetime,
    ) -> None:
        from sqlalchemy.ext.asyncio import AsyncSession

        if not isinstance(database_session, AsyncSession):
            raise TypeError("database_session must be an AsyncSession")
        if checkpoint.thread_id != turn.thread_id:
            raise SessionError(
                "A checkpoint belongs to another Thread.",
                code="checkpoint_thread_mismatch",
            )
        existing = await database_session.get(ThreadCheckpointRecord, checkpoint.checkpoint_id)
        if existing is not None:
            if (
                _checkpoint_ref(existing) != checkpoint
                or existing.owner_kind != "root_turn"
                or existing.owner_turn_id != turn.turn_id
            ):
                raise SessionError(
                    "A checkpoint identity was already used with different content.",
                    code="checkpoint_conflict",
                )
            return
        database_session.add(
            ThreadCheckpointRecord(
                checkpoint_id=checkpoint.checkpoint_id,
                thread_id=checkpoint.thread_id,
                owner_kind="root_turn",
                owner_turn_id=turn.turn_id,
                state_object_digest=checkpoint.state_object_digest,
                harness_release=checkpoint.harness_release,
                created_at=created_at,
            )
        )
        try:
            await database_session.flush()
        except IntegrityError as exc:
            raise SessionError(
                "The checkpoint could not be selected because its identity conflicts.",
                code="checkpoint_conflict",
            ) from exc


def _snapshot_reference(row: CompositionSnapshotRecord) -> SnapshotReference:
    try:
        return SnapshotReference(
            snapshot_kind=cast(Literal["agent", "environment"], row.snapshot_kind),
            logical_digest=row.logical_digest,
            object_digest=row.object_digest,
            generation_id=row.generation_id,
            root_revision=ResourceRevisionRef(
                kind=ResourceKind(row.root_resource_kind),
                resource_id=row.root_resource_id,
                content_digest=row.root_content_digest,
            ),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise StoreIntegrityError(
            "A retained composition snapshot index is invalid.",
            code="snapshot_reference_invalid",
        ) from exc


def _checkpoint_ref(row: ThreadCheckpointRecord) -> CheckpointRef:
    try:
        return CheckpointRef(
            checkpoint_id=row.checkpoint_id,
            thread_id=row.thread_id,
            state_object_digest=row.state_object_digest,
            harness_release=row.harness_release,
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise StoreIntegrityError(
            "A retained checkpoint index is invalid.",
            code="checkpoint_reference_invalid",
        ) from exc


def _turn_view(
    row: TurnRecord,
    *,
    checkpoints: dict[str, CheckpointRef],
    run_ids: tuple[str, ...],
    deferred: PendingDeferredRecord | None,
) -> TurnView:
    state = TurnState(row.state)
    if deferred is not None and state is TurnState.waiting:
        pending = PendingDeferredRef(
            object_digest=deferred.object_digest,
            request_digest=deferred.request_digest,
            request_codec_version=deferred.request_codec_version,
            source_run_id=deferred.source_run_id,
            consumed_by_run_id=deferred.consumed_by_run_id,
        )
    else:
        pending = None
    return TurnView(
        turn_id=row.turn_id,
        session_id=row.session_id,
        thread_id=row.thread_id,
        input=_load_json(row.input_json),
        state=state,
        waiting_reason=WaitingReason(row.waiting_reason) if row.waiting_reason is not None else None,
        pending_deferred=pending,
        base_checkpoint=checkpoints.get(row.base_checkpoint_id) if row.base_checkpoint_id is not None else None,
        run_ids=run_ids,
        terminal_projection=_load_json(row.terminal_projection_json),
        failure=_load_json(row.failure_json),
        selected_checkpoint=(
            checkpoints.get(row.selected_checkpoint_id) if row.selected_checkpoint_id is not None else None
        ),
        accepted_at=row.accepted_at,
        finished_at=row.finished_at,
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _load_json(value: str | None) -> JsonValue | None:
    if value is None:
        return None
    try:
        return cast(JsonValue, json.loads(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise StoreIntegrityError("Stored JSON metadata is malformed.", code="stored_json_invalid") from exc


def _require_list(value: JsonValue | None) -> list[JsonValue]:
    if not isinstance(value, list):
        raise StoreIntegrityError("Stored JSON metadata has the wrong shape.", code="stored_json_invalid")
    return value


__all__ = ["SessionRepository"]
