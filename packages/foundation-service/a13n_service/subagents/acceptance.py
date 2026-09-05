"""Fenced, authorized, atomic acceptance of asynchronous child Runs."""

from __future__ import annotations

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.models import AgentRecord, AgentRevisionRecord
from a13n_service.environments.domain import ExistingEnvironmentSelection
from a13n_service.environments.selection import child_environment_choice
from a13n_service.environments.usage import (
    add_run_with_environment,
)
from a13n_service.iam import AuthenticatedActor, AuthorizationError, WorkspaceAction, authorize_agent
from a13n_service.interactions.acceptance import (
    RunAcceptanceError,
    validate_prepared_run,
)
from a13n_service.interactions.attempts import AttemptContext, lock_attempt_authority, read_attempt_authority
from a13n_service.interactions.control_records import inbox_counter_record
from a13n_service.interactions.domain import Run, StrictModel, Thread
from a13n_service.interactions.models import RunRecord, SessionRecord, ThreadRecord
from a13n_service.interactions.objects import (
    RUN_STATE_CONTENT_TYPE,
    RunPayloadStore,
    RunStateStore,
    StaleStateWriter,
    StoredRunState,
)
from a13n_service.interactions.records import thread_record
from a13n_service.interactions.state import RunStateEnvelope
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import Clock, assume_utc, utc_now

from .authorization import ChildRunAuthorizationError, authorize_parent_child_action
from .domain import ChildRunRelationship, child_relationship_is_visible
from .models import ChildRunRelationshipRecord
from .preparation import (
    PreparedChildRunAcceptance,
    PreparedChildRunResume,
    require_frozen_subagent_edge,
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
        clock: Clock = utc_now,
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
        async with short_session(self._sessions) as database:
            parent, _, thread = await read_attempt_authority(database, authority, assume_utc(self._clock()))
            session = await _require_session(database, parent)
            parent_resource = parent.to_resource()
            thread_resource = thread.to_resource()
            await _reauthorize(
                database,
                parent=parent_resource,
                child=prepared.run,
                child_definition_id=prepared.child_definition_id,
                workspace_id=session.workspace_id,
            )
        parent_state = await self._states.read(
            prepared.run.tenant_id,
            prepared.relationship.parent_run_id,
            expected_thread_id=authority.thread_id,
        )
        _validate_new_child_parent(
            prepared,
            parent_resource,
            thread_resource,
            parent_state.envelope,
            authority,
        )
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
                    assume_utc(self._clock()),
                    lock_inbox_origins=True,
                )
                session = await _require_session(database, parent)
                parent_resource = parent.to_resource()
                await _reauthorize(
                    database,
                    parent=parent_resource,
                    child=prepared.run,
                    child_definition_id=prepared.child_definition_id,
                    workspace_id=session.workspace_id,
                )
                _validate_new_child_parent(
                    prepared,
                    parent_resource,
                    parent_thread.to_resource(),
                    parent_state.envelope,
                    authority,
                )
                edge = next(
                    edge
                    for edge in parent_state.envelope.effective_agent_config.resolved_subagents
                    if edge.name == prepared.relationship.subagent_name
                )
                choice = await child_environment_choice(database, parent=parent_resource, policy=edge.environment)
                child_run = (
                    prepared.run.model_copy(update={"environment_access": parent.environment_access})
                    if edge.environment.mode == "shared"
                    else prepared.run
                )
                database.add(thread_record(prepared.thread))
                await add_run_with_environment(
                    database,
                    run=child_run,
                    state=prepared.state,
                    workspace_id=session.workspace_id,
                    choice=choice,
                )
                database.add(child_run_relationship_record(prepared.relationship, tenant_id=prepared.run.tenant_id))
                database.add(inbox_counter_record(prepared.thread))
        except IntegrityError as error:
            raise ChildRunAcceptanceError(
                "child_run_acceptance_conflict",
                "Child Run acceptance lost a concurrent mutation",
            ) from error
        return _receipt(prepared.relationship, prepared.run.session_id)

    async def accept_resume(
        self,
        prepared: PreparedChildRunResume,
        authority: AttemptContext,
    ) -> ChildRunAcceptanceReceipt:
        """Accept one linked continuation in the retained child Thread."""

        _validate_resume_bundle(prepared)
        async with short_session(self._sessions) as database:
            parent, _, parent_thread = await read_attempt_authority(database, authority, assume_utc(self._clock()))
            session = await _require_session(database, parent)
            parent_resource = parent.to_resource()
            await _reauthorize(
                database,
                parent=parent_resource,
                child=prepared.run,
                child_definition_id=prepared.child_definition_id,
                workspace_id=session.workspace_id,
            )
        parent_state = await self._states.read(
            prepared.run.tenant_id,
            prepared.relationship.parent_run_id,
            expected_thread_id=authority.thread_id,
        )
        source_state = await self._states.read(
            prepared.run.tenant_id,
            prepared.resumed_from_child_run_id,
            expected_thread_id=prepared.run.thread_id,
        )
        _validate_parent_authority(
            run=prepared.run,
            child_state=prepared.state,
            relationship=prepared.relationship,
            parent=parent_resource,
            parent_thread=parent_thread.to_resource(),
            parent_state=parent_state.envelope,
            authority=authority,
        )
        if source_state.envelope != prepared.source_state:
            raise ChildRunAcceptanceError(
                "child_run_resume_state_conflict",
                "Retained child source state changed before continuation acceptance",
            )
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
                    assume_utc(self._clock()),
                    lock_inbox_origins=True,
                )
                session = await _require_session(database, parent)
                parent_resource = parent.to_resource()
                await _reauthorize(
                    database,
                    parent=parent_resource,
                    child=prepared.run,
                    child_definition_id=prepared.child_definition_id,
                    workspace_id=session.workspace_id,
                )
                _validate_parent_authority(
                    run=prepared.run,
                    child_state=prepared.state,
                    relationship=prepared.relationship,
                    parent=parent_resource,
                    parent_thread=parent_thread.to_resource(),
                    parent_state=parent_state.envelope,
                    authority=authority,
                )
                child_thread = await database.scalar(
                    select(ThreadRecord)
                    .where(
                        ThreadRecord.tenant_id == prepared.run.tenant_id,
                        ThreadRecord.id == prepared.run.thread_id,
                    )
                    .with_for_update()
                )
                source_run = await database.scalar(
                    select(RunRecord)
                    .where(
                        RunRecord.tenant_id == prepared.run.tenant_id,
                        RunRecord.id == prepared.resumed_from_child_run_id,
                    )
                    .with_for_update()
                )
                source_relationship = await database.scalar(
                    select(ChildRunRelationshipRecord)
                    .where(
                        ChildRunRelationshipRecord.tenant_id == prepared.run.tenant_id,
                        ChildRunRelationshipRecord.id == prepared.resumed_from_relationship_id,
                    )
                    .with_for_update()
                )
                source_parent_run = await database.scalar(
                    select(RunRecord)
                    .where(
                        RunRecord.tenant_id == prepared.run.tenant_id,
                        RunRecord.id == prepared.source_parent_run_id,
                    )
                    .with_for_update()
                )
                _validate_locked_resume_source(
                    prepared,
                    current_parent=parent_resource,
                    child_thread=child_thread,
                    source_run=source_run,
                    source_relationship=source_relationship,
                    source_parent_run=source_parent_run,
                    source_state=source_state,
                )
                assert source_run is not None and source_parent_run is not None
                await _reauthorize_resume_source(
                    database,
                    parent=parent_resource,
                    source_parent=source_parent_run.to_resource(),
                    source_child=source_run.to_resource(),
                    workspace_id=session.workspace_id,
                )
                await add_run_with_environment(
                    database,
                    run=prepared.run.model_copy(update={"environment_access": source_run.environment_access}),
                    state=prepared.state,
                    workspace_id=session.workspace_id,
                    choice=ExistingEnvironmentSelection(environment_id=source_run.environment_id)
                    if source_run.environment_id
                    else None,
                )
                database.add(child_run_relationship_record(prepared.relationship, tenant_id=prepared.run.tenant_id))
                assert child_thread is not None
                child_thread.version += 1
                child_thread.current_run_id = prepared.run.id
                child_thread.updated_at = assume_utc(self._clock())
                await database.flush()
        except IntegrityError as error:
            raise ChildRunAcceptanceError(
                "child_run_resume_conflict",
                "Child Run continuation lost a concurrent mutation",
            ) from error
        return _receipt(prepared.relationship, prepared.run.session_id)

    async def _publish_initial(
        self,
        prepared: PreparedChildRunAcceptance | PreparedChildRunResume,
    ) -> None:
        try:
            await self._states.create(prepared.run.tenant_id, prepared.state)
        except StaleStateWriter as error:
            raise ChildRunAcceptanceError(
                "child_run_state_conflict",
                "Child Run state key already contains accepted state",
            ) from error


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
    if run.delegation_id != relationship.id or run.trigger_entity_id != relationship.id:
        raise ValueError("prepared child Run does not retain its relationship correlation")


def _validate_resume_bundle(prepared: PreparedChildRunResume) -> None:
    validate_prepared_run(prepared.run, prepared.state)
    relationship = prepared.relationship
    run = prepared.run
    if (
        run.id != relationship.child_run_id
        or run.thread_id != relationship.child_thread_id
        or run.parent_run_id != prepared.resumed_from_child_run_id
        or run.lineage_kind.value != "continue"
        or run.delegation_id != relationship.id
        or run.trigger_entity_id != relationship.id
        or prepared.resumed_from_relationship_id == relationship.id
        or not prepared.source_parent_run_id
        or prepared.source_thread_version < 1
    ):
        raise ValueError("prepared resumed child Run and relationship identities do not match")


def _validate_new_child_parent(
    prepared: PreparedChildRunAcceptance,
    parent: Run,
    parent_thread: Thread,
    parent_state: RunStateEnvelope,
    authority: AttemptContext,
) -> None:
    if prepared.thread.origin_thread_id != parent.thread_id:
        raise ChildRunAcceptanceError("child_run_parent_conflict", "Child Run parent authority changed")
    _validate_parent_authority(
        run=prepared.run,
        child_state=prepared.state,
        relationship=prepared.relationship,
        parent=parent,
        parent_thread=parent_thread,
        parent_state=parent_state,
        authority=authority,
    )


def _validate_parent_authority(
    *,
    run: Run,
    child_state: RunStateEnvelope,
    relationship: ChildRunRelationship,
    parent: Run,
    parent_thread: Thread,
    parent_state: RunStateEnvelope,
    authority: AttemptContext,
) -> None:
    if (
        relationship.parent_run_id != parent.id
        or relationship.parent_run_attempt_id != authority.run_attempt_id
        or relationship.parent_run_attempt_generation != authority.fence
        or parent_thread.id != parent.thread_id
        or run.session_id != parent.session_id
        or run.authority_principal != parent.authority_principal
    ):
        raise ChildRunAcceptanceError("child_run_parent_conflict", "Child Run parent authority changed")
    edge = require_frozen_subagent_edge(parent, parent_state, relationship.subagent_name)
    if (run.agent_id, run.agent_revision_id) != (
        edge.child_agent_id,
        edge.child_agent_revision_id,
    ):
        raise ChildRunAcceptanceError("child_run_edge_conflict", "Child Run does not match the frozen parent edge")


def _validate_locked_resume_source(
    prepared: PreparedChildRunResume,
    *,
    current_parent: Run,
    child_thread: ThreadRecord | None,
    source_run: RunRecord | None,
    source_relationship: ChildRunRelationshipRecord | None,
    source_parent_run: RunRecord | None,
    source_state: StoredRunState,
) -> None:
    if child_thread is None or source_run is None or source_relationship is None or source_parent_run is None:
        raise ChildRunAcceptanceError(
            "child_run_resume_source_missing",
            "Retained child continuation source was not found",
        )
    source = source_run.to_resource()
    relationship = source_relationship.to_resource()
    source_parent = source_parent_run.to_resource()
    if (
        child_thread.version != prepared.source_thread_version
        or child_thread.session_id != prepared.run.session_id
        or child_thread.role != "child"
        or child_thread.origin_kind != "child"
        or child_thread.current_run_id != source.id
        or child_thread.head_run_id != source.id
        or relationship.id != prepared.resumed_from_relationship_id
        or relationship.parent_run_id != prepared.source_parent_run_id
        or relationship.subagent_name != prepared.relationship.subagent_name
        or relationship.child_thread_id != child_thread.id
        or relationship.child_run_id != source.id
        or source_parent.id != prepared.source_parent_run_id
        or not child_relationship_is_visible(
            relationship,
            origin_parent=source_parent,
            requesting_parent=current_parent,
        )
        or source.status.value != "completed"
        or source.thread_id != child_thread.id
        or source_state.envelope != prepared.source_state
        or source_state.envelope.harness_schema_version != prepared.state.harness_schema_version
    ):
        raise ChildRunAcceptanceError(
            "child_run_resume_source_conflict",
            "Retained child execution is no longer the selected resumable head",
        )
    sealed = source.sealed_state
    if sealed is None or (
        sealed.digest_sha256,
        sealed.size_bytes,
        sealed.content_type,
        sealed.envelope_schema_version,
        sealed.harness_schema_version,
        sealed.checkpoint_seq,
    ) != (
        source_state.digest_sha256,
        len(source_state.body),
        RUN_STATE_CONTENT_TYPE,
        source_state.envelope.schema_version,
        source_state.envelope.harness_schema_version,
        source_state.envelope.checkpoint_seq,
    ):
        raise ChildRunAcceptanceError(
            "child_run_resume_state_conflict",
            "Retained child source state does not match its sealed Run reference",
        )


async def _reauthorize_resume_source(
    database: AsyncSession,
    *,
    parent: Run,
    source_parent: Run,
    source_child: Run,
    workspace_id: str,
) -> None:
    try:
        await authorize_parent_child_action(
            database,
            parent=parent,
            source_parent_agent_ids=(source_parent.agent_id,),
            child_agent_ids=(source_child.agent_id,),
            workspace_id=workspace_id,
            action=WorkspaceAction.run_continue,
        )
    except ChildRunAuthorizationError as error:
        raise ChildRunAcceptanceError(
            "child_run_authorization_denied",
            "Persisted parent Principal is no longer authorized to continue the retained child Agent",
        ) from error


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
    child_definition_id: str,
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
            or child_definition_id != f"agent-config-{child_revision.content_digest[:24]}"
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


def _receipt(relationship: ChildRunRelationship, session_id: str) -> ChildRunAcceptanceReceipt:
    return ChildRunAcceptanceReceipt(
        relationship=relationship,
        session_id=session_id,
        child_thread_id=relationship.child_thread_id,
        child_run_id=relationship.child_run_id,
    )


__all__ = [
    "ChildRunAcceptanceError",
    "ChildRunAcceptanceReceipt",
    "ChildRunAcceptanceService",
]
