"""State-first relational acceptance for prepared durable Runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.storage import short_session, transaction

from .domain import Run, RunInputKind, RunLineageKind, RunStatus, Session, Thread, ThreadOriginKind
from .models import RunRecord, SessionRecord, ThreadRecord
from .objects import RunStateStore, StaleStateWriter
from .records import run_record, session_record, thread_record
from .state import RunStateEnvelope


class RunAcceptanceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RunAcceptanceReceipt:
    session_id: str
    thread_id: str
    thread_version: int
    run_id: str
    run_version: int
    status: RunStatus


class RunAcceptanceService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        states: RunStateStore,
        *,
        clock=None,
    ) -> None:
        self._sessions = sessions
        self._states = states
        self._clock = clock or (lambda: datetime.now(UTC))

    async def accept_new_thread(
        self,
        *,
        session: Session | None,
        thread: Thread,
        run: Run,
        state: RunStateEnvelope,
    ) -> RunAcceptanceReceipt:
        _validate_prepared_run(run, state)
        _validate_new_thread(thread, run, session)
        replay = await self._load_replay(run, accepted_thread_version=1)
        if replay is not None:
            return replay
        await self._publish_initial(run, state)
        try:
            async with transaction(self._sessions) as database:
                if session is None:
                    await _require_session(database, run)
                else:
                    database.add(session_record(session))
                if thread.origin_kind is not ThreadOriginKind.new:
                    await _require_origin(database, thread, run)
                database.add(thread_record(thread))
                database.add(run_record(run))
        except IntegrityError as error:
            return await self._reconcile_conflict(run, error, accepted_thread_version=1)
        return _receipt(thread, run)

    async def advance_thread(
        self,
        *,
        run: Run,
        state: RunStateEnvelope,
        expected_thread_version: int,
        expected_current_run_id: str,
        expected_head_run_id: str | None,
        next_head_run_id: str | None,
    ) -> RunAcceptanceReceipt:
        _validate_prepared_run(run, state)
        accepted_thread_version = expected_thread_version + 1
        replay = await self._load_replay(run, accepted_thread_version=accepted_thread_version)
        if replay is not None:
            return replay
        await self._publish_initial(run, state)
        try:
            async with transaction(self._sessions) as database:
                thread = await _lock_thread(database, run)
                _require_thread_precondition(
                    thread,
                    expected_version=expected_thread_version,
                    expected_current_run_id=expected_current_run_id,
                    expected_head_run_id=expected_head_run_id,
                )
                current = await _load_run(database, run.tenant_id, thread.current_run_id)
                if current.status in {RunStatus.accepted.value, RunStatus.running.value}:
                    raise RunAcceptanceError("thread_busy", "Thread already has active Run work")
                await _validate_advancement(database, thread, current, run, next_head_run_id=next_head_run_id)
                database.add(run_record(run))
                thread.version += 1
                thread.current_run_id = run.id
                thread.head_run_id = next_head_run_id
                thread.updated_at = self._clock()
                await database.flush()
                receipt = RunAcceptanceReceipt(
                    session_id=thread.session_id,
                    thread_id=thread.id,
                    thread_version=thread.version,
                    run_id=run.id,
                    run_version=run.version,
                    status=run.status,
                )
        except IntegrityError as error:
            return await self._reconcile_conflict(
                run,
                error,
                accepted_thread_version=accepted_thread_version,
            )
        return receipt

    async def _load_replay(self, run: Run, *, accepted_thread_version: int) -> RunAcceptanceReceipt | None:
        if run.idempotency_key is None:
            return None
        async with short_session(self._sessions) as database:
            record = await database.scalar(
                select(RunRecord).where(
                    RunRecord.tenant_id == run.tenant_id,
                    RunRecord.idempotency_key == run.idempotency_key,
                )
            )
            if record is None:
                return None
            return await _validate_replay(database, record, run, accepted_thread_version=accepted_thread_version)

    async def _publish_initial(self, run: Run, state: RunStateEnvelope) -> None:
        try:
            await self._states.create(run.tenant_id, state)
        except StaleStateWriter:
            existing = await self._states.read(run.tenant_id, run.id, expected_thread_id=run.thread_id)
            if existing.envelope != state:
                raise RunAcceptanceError(
                    "run_state_conflict",
                    "Run state key already contains different accepted state",
                ) from None

    async def _reconcile_conflict(
        self,
        run: Run,
        error: IntegrityError,
        *,
        accepted_thread_version: int,
    ) -> RunAcceptanceReceipt:
        replay = await self._load_replay(run, accepted_thread_version=accepted_thread_version)
        if replay is not None:
            return replay
        raise RunAcceptanceError("run_acceptance_conflict", "Run acceptance lost a concurrent mutation") from error


def _validate_prepared_run(run: Run, state: RunStateEnvelope) -> None:
    if run.status is not RunStatus.accepted or run.version != 1:
        raise ValueError("prepared acceptance requires a version-one accepted Run")
    if (state.run_id, state.thread_id) != (run.id, run.thread_id):
        raise ValueError("prepared Run and initial state identities do not match")
    if state.checkpoint_kind != "initial" or state.checkpoint_seq != 0:
        raise ValueError("prepared acceptance requires initial Run state")
    if (state.agent_id, state.agent_revision_id) != (run.agent_id, run.agent_revision_id):
        raise ValueError("prepared Run and state Agent selection do not match")
    if state.effective_agent_config.content_digest != run.effective_agent_config_digest:
        raise ValueError("prepared Run effective configuration digest does not match state")
    if state.runtime_lock_digest != run.runtime_lock_digest:
        raise ValueError("prepared Run Runtime lock does not match state")


def _validate_new_thread(thread: Thread, run: Run, session: Session | None) -> None:
    if thread.version != 1 or thread.queue_version != 0 or thread.head_run_id is not None:
        raise ValueError("new Thread must start at version one with an empty head and queue")
    if thread.current_run_id != run.id:
        raise ValueError("new Thread must select its first Run")
    if (thread.tenant_id, thread.session_id, thread.id) != (run.tenant_id, run.session_id, run.thread_id):
        raise ValueError("new Thread and first Run scope do not match")
    if session is not None and (session.id, session.tenant_id) != (thread.session_id, thread.tenant_id):
        raise ValueError("new Session and root Thread scope do not match")
    if thread.origin_kind is ThreadOriginKind.fork and run.lineage_kind is not RunLineageKind.fork:
        raise ValueError("fork Thread requires fork Run lineage")
    if thread.origin_kind is ThreadOriginKind.child and run.lineage_kind is not RunLineageKind.root:
        raise ValueError("independent child Thread requires root Run lineage")
    if thread.origin_kind is ThreadOriginKind.new and run.lineage_kind is not RunLineageKind.root:
        raise ValueError("new root Thread requires root Run lineage")


async def _require_session(database: AsyncSession, run: Run) -> None:
    exists = await database.scalar(
        select(SessionRecord.id).where(SessionRecord.id == run.session_id, SessionRecord.tenant_id == run.tenant_id)
    )
    if exists is None:
        raise RunAcceptanceError("session_not_found", "The interaction Session was not found")


async def _require_origin(database: AsyncSession, thread: Thread, run: Run) -> None:
    if thread.origin_run_id != run.parent_run_id and thread.origin_kind is ThreadOriginKind.fork:
        raise ValueError("fork Thread origin Run must equal its first Run parent")
    origin = await _load_run(database, thread.tenant_id, thread.origin_run_id)
    if origin.thread_id != thread.origin_thread_id:
        raise RunAcceptanceError("thread_origin_invalid", "Thread origin Run does not belong to its origin Thread")
    if thread.origin_kind is ThreadOriginKind.fork and origin.status != RunStatus.completed.value:
        raise RunAcceptanceError("thread_origin_invalid", "Thread fork source must be completed")


async def _lock_thread(database: AsyncSession, run: Run) -> ThreadRecord:
    record = await database.scalar(
        select(ThreadRecord)
        .where(ThreadRecord.tenant_id == run.tenant_id, ThreadRecord.id == run.thread_id)
        .with_for_update()
    )
    if record is None or (record.session_id, record.tenant_id) != (run.session_id, run.tenant_id):
        raise RunAcceptanceError("thread_not_found", "The interaction Thread was not found")
    return record


def _require_thread_precondition(
    thread: ThreadRecord,
    *,
    expected_version: int,
    expected_current_run_id: str,
    expected_head_run_id: str | None,
) -> None:
    if (
        thread.version != expected_version
        or thread.current_run_id != expected_current_run_id
        or thread.head_run_id != expected_head_run_id
    ):
        raise RunAcceptanceError("thread_version_conflict", "Thread advancement precondition changed")


async def _validate_advancement(
    database: AsyncSession,
    thread: ThreadRecord,
    current: RunRecord,
    run: Run,
    *,
    next_head_run_id: str | None,
) -> None:
    required_head = run.parent_run_id if run.lineage_kind is RunLineageKind.continue_ else thread.head_run_id
    if next_head_run_id != required_head:
        raise RunAcceptanceError("thread_head_invalid", "Thread advancement selected an unrelated continuation head")
    if run.retry_of_run_id is not None:
        if run.retry_of_run_id != current.id or current.status not in {
            RunStatus.failed.value,
            RunStatus.cancelled.value,
        }:
            raise RunAcceptanceError("run_retry_conflict", "Retry source is not the current failed or cancelled Run")
        _validate_retry_copy(current.to_resource(), run)
    if run.parent_run_id is None:
        if run.lineage_kind is not RunLineageKind.root or thread.head_run_id is not None:
            raise RunAcceptanceError("run_lineage_invalid", "Root-like advancement requires a null Thread head")
        if current.status not in {RunStatus.failed.value, RunStatus.cancelled.value}:
            raise RunAcceptanceError("run_lineage_invalid", "Root-like advancement requires terminal current work")
    else:
        parent = await _load_run(database, run.tenant_id, run.parent_run_id)
        if run.lineage_kind is RunLineageKind.continue_:
            expected_parent_status = (
                RunStatus.waiting.value
                if run.input_kind in {RunInputKind.waiting_feedback, RunInputKind.waiting_continue}
                else RunStatus.completed.value
            )
            if parent.thread_id != thread.id or parent.status != expected_parent_status:
                raise RunAcceptanceError(
                    "run_lineage_invalid", "Continuation parent is not an eligible same-Thread state"
                )
            if expected_parent_status == RunStatus.waiting.value:
                _validate_inherited_execution(parent.to_resource(), run)
        elif run.lineage_kind is RunLineageKind.fork:
            raise RunAcceptanceError("run_lineage_invalid", "Fork lineage can only be the first Run of a new Thread")
    if next_head_run_id is not None:
        head = await _load_run(database, run.tenant_id, next_head_run_id)
        if head.thread_id != thread.id or head.status not in {RunStatus.waiting.value, RunStatus.completed.value}:
            raise RunAcceptanceError("thread_head_invalid", "Selected Thread head is not a sealed continuation state")


def _validate_retry_copy(source: Run, candidate: Run) -> None:
    _validate_inherited_execution(source, candidate)
    immutable_intent = (
        source.parent_run_id,
        source.lineage_kind,
        source.input_kind,
        _accepted_input(source),
    )
    candidate_intent = (
        candidate.parent_run_id,
        candidate.lineage_kind,
        candidate.input_kind,
        _accepted_input(candidate),
    )
    if candidate_intent != immutable_intent:
        raise RunAcceptanceError("run_retry_invalid", "Retry must copy the terminal Run's exact accepted intent")


def _validate_inherited_execution(source: Run, candidate: Run) -> None:
    source_authority = (
        source.authority_principal,
        source.agent_id,
        source.agent_revision_id,
        source.effective_agent_config_digest,
        source.encrypted_config_payload,
        source.runtime_lock_digest,
        source.model_execution_observation,
        source.connection_selections,
        source.mcp_connection_selections,
        source.ingress_context,
        source.mcp_tool_snapshot,
    )
    candidate_authority = (
        candidate.authority_principal,
        candidate.agent_id,
        candidate.agent_revision_id,
        candidate.effective_agent_config_digest,
        candidate.encrypted_config_payload,
        candidate.runtime_lock_digest,
        candidate.model_execution_observation,
        candidate.connection_selections,
        candidate.mcp_connection_selections,
        candidate.ingress_context,
        candidate.mcp_tool_snapshot,
    )
    if candidate_authority != source_authority:
        raise RunAcceptanceError(
            "run_inherited_authority_invalid",
            "Run must preserve its source's accepted execution authority",
        )


def _accepted_input(run: Run) -> tuple[object, ...]:
    if run.input_object is not None:
        return ("object", run.input_object, run.input_text)
    return ("inline", run.input, run.input_text)


async def _load_run(database: AsyncSession, tenant_id: str, run_id: str | None) -> RunRecord:
    if run_id is None:
        raise RunAcceptanceError("run_not_found", "The interaction Run was not found")
    record = await database.scalar(select(RunRecord).where(RunRecord.tenant_id == tenant_id, RunRecord.id == run_id))
    if record is None:
        raise RunAcceptanceError("run_not_found", "The interaction Run was not found")
    return record


async def _validate_replay(
    database: AsyncSession,
    record: RunRecord,
    run: Run,
    *,
    accepted_thread_version: int,
) -> RunAcceptanceReceipt:
    if record.request_fingerprint != run.request_fingerprint:
        raise RunAcceptanceError("run_idempotency_conflict", "Idempotency key was reused with different Run intent")
    thread = await database.scalar(
        select(ThreadRecord).where(ThreadRecord.tenant_id == record.tenant_id, ThreadRecord.id == record.thread_id)
    )
    if thread is None:
        raise RunAcceptanceError("run_acceptance_corrupt", "Accepted Run has no owning Thread")
    return RunAcceptanceReceipt(
        session_id=record.session_id,
        thread_id=record.thread_id,
        thread_version=accepted_thread_version,
        run_id=record.id,
        run_version=record.version,
        status=RunStatus(record.status),
    )


def _receipt(thread: Thread, run: Run) -> RunAcceptanceReceipt:
    return RunAcceptanceReceipt(
        session_id=thread.session_id,
        thread_id=thread.id,
        thread_version=thread.version,
        run_id=run.id,
        run_version=run.version,
        status=run.status,
    )


__all__ = ["RunAcceptanceError", "RunAcceptanceReceipt", "RunAcceptanceService"]
