"""Shared Agent persistence and mutation invariants."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.application_errors import ApplicationError, ErrorCategory
from a13n_service.digests import digest_request
from a13n_service.durable_operations.idempotency import (
    IdempotencyIdentity,
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
)
from a13n_service.durable_operations.requests import ReplayReceipt, evidence_record
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

from .domain import (
    Agent,
    AgentConfig,
    AgentRevisionCreateResult,
    AgentSource,
    BuiltinAgentRegistration,
    JsonObject,
    PluginRuntimeMode,
    ResolvedRevisionContent,
)
from .errors import (
    AgentError,
    agent_archived,
    agent_not_found,
    agent_revision_not_found,
    agent_version_conflict,
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
    if record is None:
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
    if record is None:
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
        normalized_name=agent_name_key(registration.name),
        description=registration.description,
        version=1,
        current_revision_id=revision_id,
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
    mode: PluginRuntimeMode,
    config: AgentConfig,
    resolved: ResolvedRevisionContent,
    source_revision_id: str | None,
    actor: AuthenticatedActor,
    now: datetime,
) -> AgentRevisionRecord:
    config_payload = config.model_dump(mode="json", by_alias=True)
    resolved_payload = resolved.model_dump(mode="json", by_alias=True)
    content_digest = digest_request(
        {
            "plugin_runtime_mode": mode.value,
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
        plugin_runtime_mode=mode.value,
        config=config_payload,
        config_digest=digest_request(config),
        resolved_model=resolved.resolved_model.model_dump(mode="json"),
        resolved_plugin_versions=[item.model_dump(mode="json") for item in resolved.resolved_plugin_versions],
        runtime_lock_digest=resolved.runtime_lock_digest,
        resolved_skills=[item.model_dump(mode="json") for item in resolved.resolved_skills],
        connector_tools=[item.model_dump(mode="json") for item in resolved.connector_tools],
        mcp_tools=[item.model_dump(mode="json") for item in resolved.mcp_tools],
        resolved_subagents=[item.model_dump(mode="json") for item in resolved.resolved_subagents],
        content_digest=content_digest,
        source_revision_id=source_revision_id,
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
        plugin_runtime_mode=source.plugin_runtime_mode,
        config=source.config,
        config_digest=source.config_digest,
        resolved_model=source.resolved_model,
        resolved_plugin_versions=source.resolved_plugin_versions,
        runtime_lock_digest=source.runtime_lock_digest,
        resolved_skills=source.resolved_skills,
        connector_tools=source.connector_tools,
        mcp_tools=source.mcp_tools,
        resolved_subagents=source.resolved_subagents,
        content_digest=source.content_digest,
        source_revision_id=source_revision_id,
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


def require_version(record: AgentRecord, expected: int) -> None:
    if record.version != expected:
        raise agent_version_conflict(record.version)


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
    record.updated_at = now


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
                .join(AgentRecord, AgentRecord.current_revision_id == AgentRevisionRecord.id)
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
    response: BaseModel,
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
            response=response,
            now=now,
        )
    )
    session.add(
        new_agent_audit(
            actor=actor,
            organization_id=record.organization_id,
            workspace_id=record.workspace_id,
            action=operation,
            agent_id=record.id,
            now=now,
        )
    )


def request_identity(idempotency_key: str, request) -> IdempotencyIdentity:
    try:
        key_digest = digest_visible_ascii_key(idempotency_key)
    except InvalidIdempotencyKey as error:
        raise invalid_idempotency_key() from error
    return IdempotencyIdentity(key_digest, digest_request(request))


def payload_identity(idempotency_key: str, payload: JsonObject) -> IdempotencyIdentity:
    try:
        key_digest = digest_visible_ascii_key(idempotency_key)
    except InvalidIdempotencyKey as error:
        raise invalid_idempotency_key() from error
    return IdempotencyIdentity(key_digest, digest_request(payload))


async def load_replay(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    operation: str,
    scope_id: str,
    identity: IdempotencyIdentity,
    now: datetime,
) -> ReplayReceipt | None:
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
        details=None,
    )


def agent_name_key(value: str) -> str:
    return value.casefold()
