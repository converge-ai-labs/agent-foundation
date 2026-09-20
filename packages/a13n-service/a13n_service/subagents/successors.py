"""Inactive parent-Thread routing and automatic async-result successors."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import anyio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ApplicationError
from a13n_service.background import Sweep
from a13n_service.environments.websocket.admission import OnlineAdmission, OnlineEvidence
from a13n_service.environments.websocket.coordination import ConnectionCoordination
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.iam.operation import authorization_operation
from a13n_service.interactions.acceptance_validation import validate_prepared_run
from a13n_service.interactions.control_domain import RunAcceptanceReceipt, ThreadInboxEntry
from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.domain import Run
from a13n_service.interactions.environment_acceptance import add_run_with_environment
from a13n_service.interactions.environment_selection import RetainedRunEnvironment
from a13n_service.interactions.inbox import ThreadControlSignalPublisher
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.interactions.models import RunRecord, SessionRecord
from a13n_service.interactions.objects import (
    RUN_STATE_CONTENT_TYPE,
    RunObjectError,
    RunStateStore,
    StaleStateWriter,
    StoredRunState,
)
from a13n_service.interactions.ports.memory import ExecutionBindings
from a13n_service.interactions.session_scope import SessionScope
from a13n_service.labels import merge_labels
from a13n_service.run_stream import RetainedItem, RunDisplayStore
from a13n_service.storage import ObjectStoreError, short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .result_payload import (
    AsyncSubagentResultAuthority,
    AsyncSubagentResultError,
    AsyncSubagentResultItemUnavailable,
    load_async_subagent_terminal_item,
    read_async_subagent_result_authority,
    validate_async_subagent_result_authority,
)
from .successor_inbox import (
    bind_locked_unbound_async_entries,
    consume_async_result_for_successor,
    reconcile_pending_async_results,
)
from .successor_preparation import (
    PreparedAsyncResultSuccessor,
    prepare_async_result_successor,
)
from .successor_routing import (
    AsyncSubagentSuccessorError,
    AsyncSubagentSuccessorReceipt,
    LockedAsyncResultSelection,
    lock_and_route_async_result,
)

logger = logging.getLogger("a13n_service.subagents.successors")


@dataclass(frozen=True, slots=True)
class _PreparedSelection:
    entry: ThreadInboxEntry
    selected_parent: Run
    result_authority: AsyncSubagentResultAuthority
    session_scope: SessionScope


class AsyncSubagentSuccessorReconciler:
    """Route unbound results or accept one state-first continuation atomically."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        states: RunStateStore,
        displays: RunDisplayStore,
        *,
        lifecycle: LifecycleWriter,
        bindings: ExecutionBindings,
        coordination: ConnectionCoordination | None = None,
        signals: ThreadControlSignalPublisher | None = None,
        run_id_factory: Callable[[str, str, str], str] | None = None,
        clock: Clock = utc_now,
    ) -> None:
        self._after_thread_id = ""
        self._sessions = sessions
        self._online = OnlineAdmission(sessions, coordination)
        self._states = states
        self._displays = displays
        self._signals = signals
        self._lifecycle = lifecycle
        self._bindings = bindings
        self._run_id_factory = run_id_factory or _successor_run_id
        self._clock = clock

    @authorization_operation
    async def reconcile_thread(
        self,
        *,
        organization_id: str,
        thread_id: str,
    ) -> AsyncSubagentSuccessorReceipt:
        now = assume_utc(self._clock())
        selected = await self._select_or_route(organization_id=organization_id, thread_id=thread_id, now=now)
        if isinstance(selected, AsyncSubagentSuccessorReceipt):
            await self._signal_if_bound(organization_id=organization_id, receipt=selected)
            return selected
        terminal_item = await load_async_subagent_terminal_item(
            self._displays,
            organization_id=organization_id,
            child=selected.result_authority.child,
            expected_item_id=selected.result_authority.payload.terminal_result_item_id,
        )
        validate_async_subagent_result_authority(selected.result_authority, terminal_item)
        parent_state = await self._states.read_run(selected.selected_parent)
        _verify_selected_parent_state(selected.selected_parent, parent_state)
        prepared = prepare_async_result_successor(
            selected_parent=selected.selected_parent,
            selected_parent_state=parent_state.envelope,
            origin_run=selected.result_authority.parent,
            inbox_entry=selected.entry,
            successor_run_id=self._run_id_factory(
                organization_id,
                selected.entry.id,
                selected.selected_parent.id,
            ),
            created_at=now,
        )
        initial_state = await self._publish_initial(prepared)
        receipt = await self._accept(
            organization_id=organization_id,
            thread_id=thread_id,
            session_scope=selected.session_scope,
            prepared=prepared,
            parent_state=parent_state,
            initial_state=initial_state,
            terminal_item=terminal_item,
            now=now,
        )
        await self._signal_if_bound(organization_id=organization_id, receipt=receipt)
        return receipt

    async def reconcile_once(self, *, limit: int = 64) -> int:
        """Reconcile a bounded set of Threads with unbound result authority."""

        return (await self.scan(limit=limit)).completed

    async def scan(self, *, limit: int = 64, item_timeout_seconds: float = 30) -> Sweep:
        if limit < 1 or limit > 1024:
            raise ValueError("async result successor reconciliation limit is invalid")
        async with short_session(self._sessions) as database:
            candidates = tuple(
                (
                    await database.execute(
                        select(
                            ThreadInboxRecord.organization_id,
                            ThreadInboxRecord.thread_id,
                            func.min(ThreadInboxRecord.created_at).label("oldest_created_at"),
                        )
                        .where(
                            ThreadInboxRecord.kind == "async_subagent_result",
                            ThreadInboxRecord.status == "pending",
                            ThreadInboxRecord.thread_id > self._after_thread_id,
                        )
                        .group_by(ThreadInboxRecord.organization_id, ThreadInboxRecord.thread_id)
                        .order_by(ThreadInboxRecord.thread_id)
                        .limit(limit)
                    )
                )
                .tuples()
                .all()
            )
        if not candidates:
            self._after_thread_id = ""
            return Sweep()
        reconciled = 0
        for organization_id, thread_id, _ in candidates:
            self._after_thread_id = thread_id
            try:
                with anyio.fail_after(item_timeout_seconds):
                    async with transaction(self._sessions) as database:
                        finalized = await reconcile_pending_async_results(
                            database,
                            organization_id=organization_id,
                            thread_id=thread_id,
                            now=self._clock(),
                        )
                    receipt = await self.reconcile_thread(organization_id=organization_id, thread_id=thread_id)
            except AsyncSubagentResultItemUnavailable:
                logger.info(
                    "async_subagent_successor_item_deferred",
                    extra={"event": "async_subagent_successor_item_deferred", "thread_id": thread_id},
                )
                continue
            except (
                AsyncSubagentResultError,
                AsyncSubagentSuccessorError,
                RunObjectError,
                ObjectStoreError,
                ApplicationError,
                TimeoutError,
            ):
                logger.warning(
                    "async_subagent_successor_recovery_deferred",
                    extra={"event": "async_subagent_successor_recovery_deferred", "thread_id": thread_id},
                )
                continue
            reconciled += int(finalized > 0 or receipt.outcome not in {"idle", "queue_precedence"})
        return Sweep(
            examined=len(candidates),
            completed=reconciled,
            deferred=len(candidates) - reconciled,
            oldest_age_seconds=max(
                (assume_utc(self._clock()) - assume_utc(created)).total_seconds() for _, _, created in candidates
            ),
        )

    async def _select_or_route(
        self,
        *,
        organization_id: str,
        thread_id: str,
        now: datetime,
    ) -> AsyncSubagentSuccessorReceipt | _PreparedSelection:
        async with transaction(self._sessions) as database:
            selected = await lock_and_route_async_result(
                database,
                organization_id=organization_id,
                thread_id=thread_id,
                now=now,
            )
            if isinstance(selected, AsyncSubagentSuccessorReceipt):
                return selected
            entry = selected.entry.to_resource()
            authority = await read_async_subagent_result_authority(database, entry)
            session_scope = await _authorize_successor(
                database,
                selected,
                child_run_id=authority.child.id,
            )
            return _PreparedSelection(
                entry=entry,
                selected_parent=selected.selected_parent.to_resource(),
                result_authority=authority,
                session_scope=session_scope,
            )

    async def _publish_initial(self, prepared: PreparedAsyncResultSuccessor) -> StoredRunState:
        try:
            return await self._states.create(prepared.run.organization_id, prepared.state)
        except StaleStateWriter:
            existing = await self._states.read(
                prepared.run.organization_id,
                prepared.run.id,
                expected_thread_id=prepared.run.thread_id,
            )
            if existing.envelope != prepared.state:
                raise AsyncSubagentSuccessorError(
                    "automatic successor state key contains a different candidate"
                ) from None
            return existing

    async def _accept(
        self,
        *,
        organization_id: str,
        thread_id: str,
        session_scope: SessionScope,
        prepared: PreparedAsyncResultSuccessor,
        parent_state: StoredRunState,
        initial_state: StoredRunState,
        terminal_item: RetainedItem | None,
        now: datetime,
    ) -> AsyncSubagentSuccessorReceipt:
        async def accept(database: AsyncSession, online: OnlineEvidence) -> AsyncSubagentSuccessorReceipt:
            selected = await lock_and_route_async_result(
                database,
                organization_id=organization_id,
                thread_id=thread_id,
                now=now,
            )
            if isinstance(selected, AsyncSubagentSuccessorReceipt):
                return selected
            child_run_id = await _validate_final_selection(
                database,
                selected,
                prepared,
                parent_state,
                initial_state,
                terminal_item,
            )
            session = await _authorize_successor(
                database,
                selected,
                child_run_id=child_run_id,
                session_scope=session_scope,
            )
            successor_record = await add_run_with_environment(
                database,
                online=online,
                run=prepared.run.model_copy(update={"labels": merge_labels(selected.thread.labels)}),
                state=prepared.state,
                workspace_id=session.workspace_id,
                intent=RetainedRunEnvironment(selected.selected_parent.id, selected.selected_parent.thread_id),
            )
            await database.flush()
            await self._bindings.finalize(
                database, successor_record.to_resource(), source_run_id=selected.selected_parent.id
            )
            await self._lifecycle.append_accepted_run_lifecycle(database, successor_record)
            consume_async_result_for_successor(
                selected.thread,
                selected.entry,
                successor_run_id=prepared.run.id,
                state_digest_sha256=initial_state.digest_sha256,
                now=now,
            )
            bind_locked_unbound_async_entries(
                selected.later_entries,
                target_run_id=prepared.run.id,
            )
            selected.thread.version += 1
            selected.thread.current_run_id = prepared.run.id
            selected.thread.head_run_id = selected.selected_parent.id
            selected.thread.updated_at = now
            await database.flush()
            return AsyncSubagentSuccessorReceipt(
                thread_id=thread_id,
                outcome="run_accepted",
                inbox_entry_id=selected.entry.id,
                successor=RunAcceptanceReceipt(
                    session_id=selected.thread.session_id,
                    thread_id=thread_id,
                    thread_version=selected.thread.version,
                    run_id=prepared.run.id,
                    run_version=prepared.run.version,
                ),
            )

        try:
            return await self._online.commit(accept)
        except IntegrityError as error:
            raise AsyncSubagentSuccessorError(
                "automatic result successor lost a concurrent relational mutation"
            ) from error

    async def _signal_if_bound(
        self,
        *,
        organization_id: str,
        receipt: AsyncSubagentSuccessorReceipt,
    ) -> None:
        if self._signals is None or receipt.outcome != "bound_active":
            return
        try:
            await self._signals.publish(organization_id=organization_id, thread_id=receipt.thread_id)
        except Exception:
            logger.warning(
                "async_subagent_successor_signal_failed",
                extra={"event": "async_subagent_successor_signal_failed", "thread_id": receipt.thread_id},
                exc_info=True,
            )


async def _validate_final_selection(
    database: AsyncSession,
    selected: LockedAsyncResultSelection,
    prepared: PreparedAsyncResultSuccessor,
    parent_state: StoredRunState,
    initial_state: StoredRunState,
    terminal_item: RetainedItem | None,
) -> str:
    authority = await read_async_subagent_result_authority(database, selected.entry.to_resource())
    payload = validate_async_subagent_result_authority(authority, terminal_item)
    _verify_selected_parent_state(selected.selected_parent.to_resource(), parent_state)
    expected = prepare_async_result_successor(
        selected_parent=selected.selected_parent.to_resource(),
        selected_parent_state=parent_state.envelope,
        origin_run=selected.origin.to_resource(),
        inbox_entry=selected.entry.to_resource(),
        successor_run_id=prepared.run.id,
        created_at=prepared.run.created_at,
    )
    if expected != prepared or initial_state.envelope != prepared.state:
        raise AsyncSubagentSuccessorError("automatic successor preparation no longer matches authority")
    validate_prepared_run(prepared.run, prepared.state)
    return payload.child_run_id


async def _authorize_successor(
    database: AsyncSession,
    selected: LockedAsyncResultSelection,
    *,
    child_run_id: str,
    session_scope: SessionScope | None = None,
) -> SessionScope:
    if session_scope is None:
        record = await database.scalar(
            select(SessionRecord).where(
                SessionRecord.organization_id == selected.thread.organization_id,
                SessionRecord.id == selected.thread.session_id,
            )
        )
        session_scope = None if record is None else SessionScope.from_record(record)
    session = session_scope
    child = await database.scalar(
        select(RunRecord).where(
            RunRecord.organization_id == selected.thread.organization_id,
            RunRecord.id == child_run_id,
        )
    )
    if (
        session is None
        or child is None
        or session.organization_id != selected.thread.organization_id
        or session.id != selected.thread.session_id
        or selected.selected_parent.session_id != session.id
        or selected.origin.session_id != session.id
        or child.session_id != session.id
    ):
        raise AsyncSubagentSuccessorError("automatic successor Session authority is incomplete")
    actor = AuthenticatedActor(
        principal=selected.origin.to_resource().authority_principal,
        auth_method="run_authority",
        credential_id=f"run_{selected.origin.id}",
        boundary_workspace_id=session.workspace_id,
        request_id=selected.entry.id,
    )
    try:
        await authorize_agent(
            database,
            actor=actor,
            workspace_id=session.workspace_id,
            agent_id=selected.selected_parent.agent_id,
            action=WorkspaceAction.run_read,
        )
        await authorize_agent(
            database,
            actor=actor,
            workspace_id=session.workspace_id,
            agent_id=selected.selected_parent.agent_id,
            action=WorkspaceAction.run_continue,
        )
        await authorize_agent(
            database,
            actor=actor,
            workspace_id=session.workspace_id,
            agent_id=selected.selected_parent.agent_id,
            action=WorkspaceAction.agent_invoke,
        )
        await authorize_agent(
            database,
            actor=actor,
            workspace_id=session.workspace_id,
            agent_id=child.agent_id,
            action=WorkspaceAction.run_read,
        )
    except AuthorizationError as error:
        raise AsyncSubagentSuccessorError("automatic successor is no longer authorized") from error
    return session


def _verify_selected_parent_state(parent: Run, state: StoredRunState) -> None:
    sealed = parent.sealed_state
    if sealed is None or (
        sealed.digest_sha256,
        sealed.size_bytes,
        sealed.envelope_schema_version,
        sealed.harness_schema_version,
        sealed.checkpoint_seq,
        sealed.content_type,
    ) != (
        state.digest_sha256,
        len(state.body),
        state.envelope.schema_version,
        state.envelope.harness_schema_version,
        state.envelope.checkpoint_seq,
        RUN_STATE_CONTENT_TYPE,
    ):
        raise AsyncSubagentSuccessorError("selected parent state does not match its sealed reference")


def _successor_run_id(organization_id: str, entry_id: str, parent_run_id: str) -> str:
    digest = hashlib.sha256(f"{organization_id}:{entry_id}:{parent_run_id}".encode()).hexdigest()
    return f"run_{digest[:24]}"


__all__ = [
    "AsyncSubagentSuccessorError",
    "AsyncSubagentSuccessorReceipt",
    "AsyncSubagentSuccessorReconciler",
]
