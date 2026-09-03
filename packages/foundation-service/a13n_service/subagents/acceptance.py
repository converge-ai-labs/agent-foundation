"""Fenced, authorized, atomic acceptance of asynchronous child Runs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.interactions.acceptance import RunAcceptanceError, validate_prepared_run
from a13n_service.interactions.attempts import AttemptContext, lock_attempt_authority, read_attempt_authority
from a13n_service.interactions.control_records import inbox_counter_record
from a13n_service.interactions.domain import Run, StrictModel, Thread
from a13n_service.interactions.environment_bindings import (
    add_run_with_environment_binding,
    verify_environment_binding,
)
from a13n_service.interactions.models import RunRecord, SessionRecord
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore, StaleStateWriter
from a13n_service.interactions.records import thread_record
from a13n_service.interactions.state import RunStateEnvelope
from a13n_service.storage import short_session, transaction

from .domain import ChildRunRelationship
from .models import ChildRunRelationshipRecord
from .preparation import (
    PreparedChildRunAcceptance,
    require_frozen_subagent_edge,
    validate_child_environment_policy,
)
from .records import child_run_relationship_record


class ChildRunAcceptanceError(RunAcceptanceError):
    """A child acceptance candidate no longer matches durable authority."""


class ChildRunAcceptanceReceipt(StrictModel):
    relationship: ChildRunRelationship
    session_id: str = Field(min_length=1, max_length=72)
    child_thread_id: str = Field(min_length=1, max_length=72)
    child_run_id: str = Field(min_length=1, max_length=72)


class ChildRunAcceptanceService:
    """Accept one prepared child under the spawning Attempt's live fence."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        states: RunStateStore,
        payloads: RunPayloadStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._states = states
        self._payloads = payloads
        self._clock = clock

    async def accept(
        self,
        prepared: PreparedChildRunAcceptance,
        authority: AttemptContext,
    ) -> ChildRunAcceptanceReceipt:
        _validate_bundle(prepared)
        replay = await self._load_replay(prepared)
        if replay is not None:
            return replay
        async with short_session(self._sessions) as database:
            parent, _, thread = await read_attempt_authority(database, authority, _utc(self._clock()))
            session = await _require_session(database, parent)
            parent_resource = parent.to_resource()
            thread_resource = thread.to_resource()
            await _reauthorize(
                database,
                parent=parent_resource,
                child=prepared.run,
                workspace_id=session.workspace_id,
            )
        parent_state = await self._states.read(
            prepared.run.tenant_id,
            prepared.relationship.parent_run_id,
            expected_thread_id=authority.thread_id,
        )
        _validate_locked_parent(prepared, parent_resource, thread_resource, parent_state.envelope, authority)
        if prepared.run.input_object is not None:
            await self._payloads.verify_reference(
                prepared.run.tenant_id,
                prepared.run.id,
                "input",
                prepared.run.input_object,
            )
        await self._publish_initial(prepared)
        try:
            async with transaction(self._sessions) as database:
                parent, _, parent_thread = await lock_attempt_authority(
                    database,
                    authority,
                    _utc(self._clock()),
                    lock_inbox_origins=True,
                )
                session = await _require_session(database, parent)
                parent_resource = parent.to_resource()
                await _reauthorize(
                    database,
                    parent=parent_resource,
                    child=prepared.run,
                    workspace_id=session.workspace_id,
                )
                _validate_locked_parent(
                    prepared,
                    parent_resource,
                    parent_thread.to_resource(),
                    parent_state.envelope,
                    authority,
                )
                database.add(thread_record(prepared.thread))
                await add_run_with_environment_binding(
                    database,
                    run=prepared.run,
                    state=prepared.state,
                    workspace_id=session.workspace_id,
                )
                database.add(child_run_relationship_record(prepared.relationship, tenant_id=prepared.run.tenant_id))
                database.add(inbox_counter_record(prepared.thread))
        except IntegrityError as error:
            replay = await self._load_replay(prepared)
            if replay is not None:
                return replay
            raise ChildRunAcceptanceError(
                "child_run_acceptance_conflict",
                "Child Run acceptance lost a concurrent mutation",
            ) from error
        return _receipt(prepared.relationship, prepared.run.session_id)

    async def _publish_initial(self, prepared: PreparedChildRunAcceptance) -> None:
        try:
            await self._states.create(prepared.run.tenant_id, prepared.state)
        except StaleStateWriter:
            existing = await self._states.read(
                prepared.run.tenant_id,
                prepared.run.id,
                expected_thread_id=prepared.run.thread_id,
            )
            if existing.envelope != prepared.state:
                raise ChildRunAcceptanceError(
                    "child_run_state_conflict",
                    "Child Run state key already contains different accepted state",
                ) from None

    async def _load_replay(
        self,
        prepared: PreparedChildRunAcceptance,
    ) -> ChildRunAcceptanceReceipt | None:
        relationship = prepared.relationship
        async with short_session(self._sessions) as database:
            record = await database.scalar(
                select(ChildRunRelationshipRecord).where(
                    ChildRunRelationshipRecord.tenant_id == prepared.run.tenant_id,
                    ChildRunRelationshipRecord.parent_run_id == relationship.parent_run_id,
                    ChildRunRelationshipRecord.spawn_operation_id == relationship.spawn_operation_id,
                )
            )
            if record is None:
                return None
            existing = record.to_resource()
            child = await database.scalar(
                select(RunRecord).where(
                    RunRecord.tenant_id == prepared.run.tenant_id,
                    RunRecord.id == existing.child_run_id,
                )
            )
            parent = await database.scalar(
                select(RunRecord).where(
                    RunRecord.tenant_id == prepared.run.tenant_id,
                    RunRecord.id == existing.parent_run_id,
                )
            )
            if child is None or parent is None:
                raise ChildRunAcceptanceError("child_run_replay_corrupt", "Accepted child relationship is incomplete")
            session = await _require_session(database, parent)
            parent_resource = parent.to_resource()
            child_resource = child.to_resource()
            await _reauthorize(
                database,
                parent=parent_resource,
                child=child_resource,
                workspace_id=session.workspace_id,
            )
            _validate_replay_intent(existing, child, prepared)
        state = await self._states.read(
            child_resource.tenant_id,
            child_resource.id,
            expected_thread_id=child_resource.thread_id,
        )
        validate_prepared_run(child_resource, state.envelope)
        async with short_session(self._sessions) as database:
            await verify_environment_binding(database, run=child_resource, state=state.envelope)
        return _receipt(existing, child_resource.session_id)


def _validate_bundle(prepared: PreparedChildRunAcceptance) -> None:
    validate_prepared_run(prepared.run, prepared.state)
    relationship = prepared.relationship
    thread = prepared.thread
    run = prepared.run
    if (
        thread.id != relationship.child_thread_id
        or run.id != relationship.child_run_id
        or thread.current_run_id != run.id
        or thread.origin_run_id != relationship.parent_run_id
        or thread.origin_thread_id is None
        or thread.session_id != run.session_id
        or thread.tenant_id != run.tenant_id
    ):
        raise ValueError("prepared child Thread, Run, and relationship identities do not match")
    if (
        run.parent_tool_call_id != relationship.spawn_operation_id
        or run.delegation_id != relationship.id
        or run.trigger_entity_id != relationship.id
    ):
        raise ValueError("prepared child Run does not retain its spawning operation correlation")


def _validate_locked_parent(
    prepared: PreparedChildRunAcceptance,
    parent: Run,
    parent_thread: Thread,
    parent_state: RunStateEnvelope,
    authority: AttemptContext,
) -> None:
    relationship = prepared.relationship
    if (
        relationship.parent_run_id != parent.id
        or relationship.parent_run_attempt_id != authority.run_attempt_id
        or relationship.parent_run_attempt_generation != authority.fence
        or prepared.thread.origin_thread_id != parent.thread_id
        or parent_thread.id != parent.thread_id
        or prepared.run.session_id != parent.session_id
        or prepared.run.authority_principal != parent.authority_principal
    ):
        raise ChildRunAcceptanceError("child_run_parent_conflict", "Child Run parent authority changed")
    edge = require_frozen_subagent_edge(parent, parent_state, relationship.subagent_name)
    if (prepared.run.agent_id, prepared.run.agent_revision_id) != (
        edge.child_agent_id,
        edge.child_agent_revision_id,
    ):
        raise ChildRunAcceptanceError("child_run_edge_conflict", "Child Run does not match the frozen parent edge")
    validate_child_environment_policy(
        edge,
        parent=parent_state.effective_agent_config,
        child=prepared.state.effective_agent_config,
    )


async def _require_session(database: AsyncSession, parent: RunRecord) -> SessionRecord:
    record = await database.scalar(
        select(SessionRecord).where(
            SessionRecord.tenant_id == parent.tenant_id,
            SessionRecord.id == parent.session_id,
        )
    )
    if record is None:
        raise ChildRunAcceptanceError("child_run_session_missing", "Parent Session was not found")
    return record


async def _reauthorize(
    database: AsyncSession,
    *,
    parent: Run,
    child: Run,
    workspace_id: str,
) -> None:
    actor = AuthenticatedActor(
        principal=parent.authority_principal,
        auth_method="run_authority",
        credential_id=f"run_{parent.id}",
        boundary_workspace_id=workspace_id,
        request_id=parent.id,
    )
    try:
        await authorize_agent(
            database,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=parent.agent_id,
            action=WorkspaceAction.agent_invoke,
        )
        await authorize_agent(
            database,
            actor=actor,
            workspace_id=workspace_id,
            agent_id=child.agent_id,
            action=WorkspaceAction.agent_invoke,
        )
        child_agent = await database.scalar(
            select(AgentRecord).where(
                AgentRecord.organization_id == parent.tenant_id,
                AgentRecord.workspace_id == workspace_id,
                AgentRecord.id == child.agent_id,
                AgentRecord.enabled.is_(True),
                AgentRecord.archived_at.is_(None),
            )
        )
        child_revision = await database.scalar(
            select(AgentRevisionRecord).where(
                AgentRevisionRecord.organization_id == parent.tenant_id,
                AgentRevisionRecord.workspace_id == workspace_id,
                AgentRevisionRecord.agent_id == child.agent_id,
                AgentRevisionRecord.id == child.agent_revision_id,
            )
        )
        if (
            child_agent is None
            or child_revision is None
            or child_revision.runtime_lock_digest != child.runtime_lock_digest
        ):
            raise ChildRunAcceptanceError(
                "child_run_revision_unavailable",
                "Frozen child Agent revision or Runtime lock is no longer executable",
            )
    except AuthorizationError as error:
        raise ChildRunAcceptanceError(
            "child_run_authorization_denied",
            "Persisted parent Principal is no longer authorized to invoke the child Agent",
        ) from error


def _validate_replay_intent(
    existing: ChildRunRelationship,
    child: RunRecord,
    prepared: PreparedChildRunAcceptance,
) -> None:
    candidate = prepared.relationship
    if (
        existing.subagent_name != candidate.subagent_name
        or existing.cancellation_policy is not candidate.cancellation_policy
        or existing.result_visibility is not candidate.result_visibility
        or child.request_fingerprint != prepared.run.request_fingerprint
    ):
        raise ChildRunAcceptanceError(
            "child_run_idempotency_conflict",
            "Spawning operation was reused with different child Run intent",
        )


def _receipt(relationship: ChildRunRelationship, session_id: str) -> ChildRunAcceptanceReceipt:
    return ChildRunAcceptanceReceipt(
        relationship=relationship,
        session_id=session_id,
        child_thread_id=relationship.child_thread_id,
        child_run_id=relationship.child_run_id,
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "ChildRunAcceptanceError",
    "ChildRunAcceptanceReceipt",
    "ChildRunAcceptanceService",
]
