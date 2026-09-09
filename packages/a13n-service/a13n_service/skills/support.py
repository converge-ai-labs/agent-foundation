"""Shared authorization, replay, and audit primitives for Skill Management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.application_errors import ErrorCategory
from a13n_service.durable_operations.idempotency import (
    EvidenceScope,
    IdempotencyConflict,
    IdempotencyIdentity,
    InvalidIdempotencyKey,
)
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.durable_operations.requests import evidence_record, load_receipt, request_scope
from a13n_service.iam.audit import security_audit_record
from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    AuthorizedWorkspace,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.iam.models import SecurityAuditRecord, WorkspaceRecord
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction

from .errors import SkillError


@dataclass(frozen=True, slots=True)
class IdempotencyScope:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    operation: str
    resource_scope_id: str
    identity: IdempotencyIdentity


class ReplayResult[Result: BaseModel](BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    result: Result = Field(validation_alias="response", serialization_alias="response")
    created: StrictBool


def idempotency_identity(key: str, request: dict[str, str] | BaseModel) -> IdempotencyIdentity:
    try:
        return IdempotencyIdentity.from_request(key, request)
    except InvalidIdempotencyKey as error:
        raise _invalid_idempotency_key() from error


async def load_replay[ResponseModel: BaseModel](
    session: AsyncSession,
    *,
    scope: IdempotencyScope,
    now: datetime,
    response_model: type[ResponseModel],
) -> ReplayResult[ResponseModel] | None:
    try:
        receipt = await load_receipt(session, scope=_evidence_scope(scope), identity=scope.identity, now=now)
    except IdempotencyConflict as error:
        raise SkillError(
            "idempotency_conflict",
            "The Idempotency-Key was already used with different request content.",
            category=ErrorCategory.conflict,
        ) from error
    if receipt is None:
        return None
    return receipt.restore(ReplayResult[response_model])


def new_replay_evidence(
    *,
    scope: IdempotencyScope,
    response: BaseModel,
    created: bool,
    now: datetime,
) -> IdempotencyEvidenceRecord:
    return evidence_record(
        actor=scope.actor,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        operation=scope.operation,
        scope_id=scope.resource_scope_id,
        identity=scope.identity,
        result_kind="skill_command",
        result_ref=scope.resource_scope_id,
        response=ReplayResult(result=response, created=created),
        now=now,
    )


def _evidence_scope(scope: IdempotencyScope) -> EvidenceScope:
    return request_scope(
        scope.actor,
        workspace_id=scope.workspace_id,
        operation=scope.operation,
        scope_id=scope.resource_scope_id,
    )


async def authorize_skill_workspace(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
    concealed_code: str = "resource_not_found",
) -> AuthorizedWorkspace:
    try:
        return await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        raise SkillError(
            concealed_code if error.concealed else "permission_denied",
            "The requested resource was not found." if error.concealed else "Permission denied.",
            category=ErrorCategory.not_found if error.concealed else ErrorCategory.forbidden,
        ) from error


def skill_audit_record(
    *,
    actor: AuthenticatedActor,
    organization_id: str | None,
    workspace_id: str,
    skill_id: str | None,
    action: str,
    now: datetime,
    outcome: str = "success",
    details: dict[str, object] | None = None,
) -> SecurityAuditRecord:
    return security_audit_record(
        audit_id=new_object_id("aud"),
        actor=actor,
        organization_id=organization_id,
        workspace_id=workspace_id,
        action=action,
        resource_type="skill",
        resource_id=skill_id,
        outcome=outcome,
        occurred_at=now,
        details=details,
    )


def is_skill_key_race(error: IntegrityError) -> bool:
    """Return whether a create lost the active Workspace key race."""

    diagnostic = getattr(getattr(error, "orig", None), "diag", None)
    if diagnostic is not None:
        return getattr(diagnostic, "constraint_name", None) == "uq_skills_workspace_key_active"
    message = str(error.orig).casefold()
    return "unique constraint failed" in message and "skills.workspace_id, skills.key" in message


async def record_failed_skill_attempt(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    skill_id: str | None,
    action: str,
    now: datetime,
    details: dict[str, object] | None = None,
) -> None:
    async with transaction(sessions) as session:
        organization_id = await session.scalar(
            select(WorkspaceRecord.organization_id).where(WorkspaceRecord.id == workspace_id)
        )
        session.add(
            skill_audit_record(
                actor=actor,
                organization_id=organization_id,
                workspace_id=workspace_id,
                skill_id=skill_id,
                action=action,
                now=now,
                outcome="failure",
                details=details,
            )
        )


def _invalid_idempotency_key() -> SkillError:
    return SkillError(
        "invalid_request",
        "Idempotency-Key must contain 1 through 512 visible ASCII bytes.",
        category=ErrorCategory.invalid_request,
    )
