"""Shared Agent persistence and mutation invariants."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.digests import digest_request
from a13n_service.durable_operations.idempotency import (
    IdempotencyIdentity,
    InvalidIdempotencyKey,
)
from a13n_service.durable_operations.requests import ReplayReference, evidence_record
from a13n_service.durable_operations.requests import load_replay as load_request_replay
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam import (
    AuthenticatedActor,
    AuthorizationError,
    authorize_agent,
)
from a13n_service.iam.audit import security_audit_record
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id
from a13n_service.temporal import next_updated_at

from .domain import (
    Agent,
    AgentConfig,
    AgentRevisionCreateResult,
    AgentSource,
    BuiltinAgentRegistration,
    ResolvedRevisionContent,
)
from .errors import (
    AgentError,
    agent_archived,
    agent_not_found,
    agent_revision_not_found,
    idempotency_conflict,
    invalid_idempotency_key,
    map_authorization_error,
)
from .models import AgentRecord, AgentRevisionRecord


async def authorize_agent_scope(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    agent_id: str,
    action: WorkspaceAction,
):
    try:
        return await authorize_agent(
            session,
            actor=actor,
            workspace_id=actor.workspace_id,
            agent_id=agent_id,
            action=action,
        )
    except AuthorizationError as error:
        raise map_authorization_error(error, exact=True) from error


async def load_agent(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    agent_id: str,
) -> Agent:
    record = await session.scalar(
        select(AgentRecord).where(
            AgentRecord.id == agent_id,
            AgentRecord.organization_id == organization_id,
            AgentRecord.workspace_id == workspace_id,
        )
    )
    if record is None or record.system_purpose is not None:
        raise agent_not_found()
    return record.to_resource()


async def lock_agent(
    session: AsyncSession,
    organization_id: str,
    workspace_id: str,
    agent_id: str,
) -> AgentRecord:
    record = await session.scalar(
        select(AgentRecord)
        .where(
            AgentRecord.id == agent_id,
            AgentRecord.organization_id == organization_id,
            AgentRecord.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if record is None or record.system_purpose is not None:
        raise agent_not_found()
    return record


async def lock_revision(
    session: AsyncSession,
    *,
    organization_id: str,
    workspace_id: str,
    agent_id: str,
    revision_id: str,
) -> AgentRevisionRecord:
    record = await session.scalar(
        select(AgentRevisionRecord)
        .where(
            AgentRevisionRecord.id == revision_id,
            AgentRevisionRecord.agent_id == agent_id,
            AgentRevisionRecord.organization_id == organization_id,
            AgentRevisionRecord.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    if record is None:
        raise agent_revision_not_found()
    return record


async def next_revision_number(session: AsyncSession, agent_id: str) -> int:
    highest = await session.scalar(
        select(func.max(AgentRevisionRecord.version)).where(AgentRevisionRecord.agent_id == agent_id)
    )
    return (highest or 0) + 1


def new_builtin_agent(
    *,
    organization_id: str,
    workspace_id: str,
    revision_id: str,
    registration: BuiltinAgentRegistration,
    now: datetime,
) -> AgentRecord:
    return AgentRecord(
        id=registration.agent_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        source=AgentSource.builtin.value,
        name=registration.name,
        description=registration.description,
        default_revision_id=revision_id,
        enabled=True,
        archived_at=None,
        duplicated_from_agent_id=None,
        duplicated_from_revision_id=None,
        created_by_type="system",
        created_by_id=registration.system_actor_id,
        updated_by_type="system",
        updated_by_id=registration.system_actor_id,
        created_at=now,
        updated_at=now,
    )


def new_revision(
    agent: AgentRecord,
    *,
    revision_id: str,
    version: int,
    config: AgentConfig,
    resolved: ResolvedRevisionContent,
    source_revision_id: str | None,
    change_summary: str | None = None,
    actor: AuthenticatedActor,
    now: datetime,
) -> AgentRevisionRecord:
    config_payload = config.model_dump(mode="json", by_alias=True)
    resolved_payload = resolved.model_dump(mode="json", by_alias=True)
    content_digest = digest_request(
        {
            "config": config_payload,
            "resolved": resolved_payload,
        }
    )
    return AgentRevisionRecord(
        id=revision_id,
        organization_id=agent.organization_id,
        workspace_id=agent.workspace_id,
        agent_id=agent.id,
        version=version,
        config=config_payload,
        config_digest=digest_request(config),
        resolved_model=resolved.resolved_model.model_dump(mode="json"),
        resolved_skills=[item.model_dump(mode="json") for item in resolved.resolved_skills],
        connection_tools=[item.model_dump(mode="json") for item in resolved.connection_tools],
        resolved_subagents=[item.model_dump(mode="json") for item in resolved.resolved_subagents],
        content_digest=content_digest,
        source_revision_id=source_revision_id,
        change_summary=change_summary,
        created_by_type=actor.principal.principal_type.value,
        created_by_id=actor.principal.principal_id,
        created_at=now,
    )


def copy_revision(
    source: AgentRevisionRecord,
    *,
    revision_id: str,
    version: int,
    source_revision_id: str,
    actor: AuthenticatedActor,
    now: datetime,
    agent_id: str | None = None,
) -> AgentRevisionRecord:
    return AgentRevisionRecord(
        id=revision_id,
        organization_id=source.organization_id,
        workspace_id=source.workspace_id,
        agent_id=agent_id or source.agent_id,
        version=version,
        config=source.config,
        config_digest=source.config_digest,
        resolved_model=source.resolved_model,
        resolved_skills=source.resolved_skills,
        connection_tools=source.connection_tools,
        resolved_subagents=source.resolved_subagents,
        content_digest=source.content_digest,
        source_revision_id=source_revision_id,
        change_summary=None,
        created_by_type=actor.principal.principal_type.value,
        created_by_id=actor.principal.principal_id,
        created_at=now,
    )


async def load_revision_create_result(session: AsyncSession, revision_id: str) -> AgentRevisionCreateResult:
    revision = await session.scalar(select(AgentRevisionRecord).where(AgentRevisionRecord.id == revision_id))
    if revision is None:
        raise idempotency_conflict()
    agent = await load_agent(
        session,
        organization_id=revision.organization_id,
        workspace_id=revision.workspace_id,
        agent_id=revision.agent_id,
    )
    return AgentRevisionCreateResult(agent=agent, revision=revision.to_resource())


def require_custom(agent: Agent) -> None:
    if agent.source is AgentSource.builtin:
        raise AgentError("agent_state_conflict", "Built-in Agents are read-only.", category=ErrorCategory.conflict)


def require_custom_mutable(record: AgentRecord) -> None:
    if record.source != AgentSource.custom.value:
        raise AgentError("agent_state_conflict", "Built-in Agents are read-only.", category=ErrorCategory.conflict)
    if record.archived_at is not None:
        raise agent_archived()


def require_etag(record: AgentRecord, if_match: str) -> None:
    current = resource_etag(record.id, record.updated_at)
    if not etag_matches(if_match, current):
        raise AgentError(
            "precondition_failed",
            "The Agent representation has changed.",
            category=ErrorCategory.stale_version,
            details={"current_etag": current},
        )


def touch_agent(record: AgentRecord, *, actor: AuthenticatedActor, now: datetime) -> None:
    record.updated_by_type = actor.principal.principal_type.value
    record.updated_by_id = actor.principal.principal_id
    record.updated_at = next_updated_at(record.updated_at, now)


def apply_lifecycle_transition(
    record: AgentRecord,
    *,
    action: Literal["enable", "disable", "archive", "unarchive"],
    now: datetime,
) -> None:
    if action == "enable":
        if record.archived_at is not None or record.enabled:
            raise AgentError(
                "agent_state_conflict",
                "The Agent cannot be enabled from its current state.",
                category=ErrorCategory.conflict,
            )
        record.enabled = True
        return
    if action == "disable":
        if record.archived_at is not None or not record.enabled:
            raise AgentError(
                "agent_state_conflict",
                "The Agent cannot be disabled from its current state.",
                category=ErrorCategory.conflict,
            )
        record.enabled = False
        return
    if action == "archive":
        if record.source != AgentSource.custom.value or record.enabled or record.archived_at is not None:
            raise AgentError(
                "agent_state_conflict", "Only a disabled custom Agent can be archived.", category=ErrorCategory.conflict
            )
        record.archived_at = now
        return
    if record.archived_at is None or record.source != AgentSource.custom.value:
        raise AgentError(
            "agent_state_conflict",
            "The Agent cannot be unarchived from its current state.",
            category=ErrorCategory.conflict,
        )
    record.archived_at = None
    record.enabled = False


async def require_not_in_use(session: AsyncSession, target: AgentRecord) -> None:
    current_revisions = tuple(
        (
            await session.scalars(
                select(AgentRevisionRecord)
                .join(AgentRecord, AgentRecord.default_revision_id == AgentRevisionRecord.id)
                .where(
                    AgentRecord.organization_id == target.organization_id,
                    AgentRecord.workspace_id == target.workspace_id,
                    AgentRecord.enabled.is_(True),
                    AgentRecord.archived_at.is_(None),
                    AgentRecord.id != target.id,
                )
            )
        ).all()
    )
    pending = list(current_revisions)
    visited: set[str] = set()
    while pending:
        revision = pending.pop()
        if revision.id in visited:
            continue
        visited.add(revision.id)
        child_revision_ids: list[str] = []
        for edge in revision.resolved_subagents:
            if edge.get("child_agent_id") == target.id:
                raise AgentError(
                    "agent_in_use",
                    "The Agent is referenced by an enabled Agent graph.",
                    category=ErrorCategory.conflict,
                )
            child_revision_id = edge.get("child_agent_revision_id")
            if isinstance(child_revision_id, str) and child_revision_id not in visited:
                child_revision_ids.append(child_revision_id)
        if child_revision_ids:
            pending.extend(
                (
                    await session.scalars(
                        select(AgentRevisionRecord).where(
                            AgentRevisionRecord.organization_id == target.organization_id,
                            AgentRevisionRecord.workspace_id == target.workspace_id,
                            AgentRevisionRecord.id.in_(child_revision_ids),
                        )
                    )
                ).all()
            )


def add_command_evidence_and_audit(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    record: AgentRecord,
    operation: str,
    identity: IdempotencyIdentity,
    result_kind: str,
    result_ref: str,
    now: datetime,
    audit_details: dict[str, object] | None = None,
    audit: bool = True,
) -> None:
    session.add(
        evidence_record(
            actor=actor,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            operation=operation,
            scope_id=record.id,
            identity=identity,
            result_kind=result_kind,
            result_ref=result_ref,
            now=now,
        )
    )
    if audit:
        session.add(
            new_agent_audit(
                actor=actor,
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                action=operation,
                agent_id=record.id,
                now=now,
                details=audit_details,
            )
        )


def request_identity(idempotency_key: str) -> IdempotencyIdentity:
    try:
        return IdempotencyIdentity.from_key(idempotency_key)
    except InvalidIdempotencyKey as error:
        raise invalid_idempotency_key() from error


async def load_replay(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    operation: str,
    scope_id: str,
    identity: IdempotencyIdentity,
    now: datetime,
) -> ReplayReference | None:
    # Agent commands require a Workspace even though shared receipts also support Organizations.
    _ = actor.workspace_id
    try:
        return await load_request_replay(
            session,
            actor=actor,
            operation=operation,
            scope_id=scope_id,
            identity=identity,
            now=now,
        )
    except ApplicationError as error:
        if error.code == "idempotency_conflict":
            raise idempotency_conflict() from error
        raise


def new_agent_audit(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    action: str,
    agent_id: str,
    now: datetime,
    details: dict[str, object] | None = None,
) -> SecurityAuditRecord:
    return security_audit_record(
        audit_id=new_object_id("audit"),
        actor=actor,
        organization_id=organization_id,
        workspace_id=workspace_id,
        action=action,
        resource_type="agent",
        resource_id=agent_id,
        outcome="success",
        occurred_at=now,
        details=details,
    )


async def created_agent_result(session: AsyncSession, agent: AgentRecord) -> AgentRevisionCreateResult:
    revision = await session.scalar(
        select(AgentRevisionRecord).where(AgentRevisionRecord.agent_id == agent.id, AgentRevisionRecord.version == 1)
    )
    if revision is None:
        raise agent_not_found()
    return AgentRevisionCreateResult(agent=agent.to_resource(), revision=revision.to_resource())
