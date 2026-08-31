"""Shared authorization, replay, and audit primitives for Skill Management."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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

from .errors import SkillManagementError
from .models import SkillIdempotencyRecord

IDEMPOTENCY_LIFETIME = timedelta(hours=24)
_MAX_IDEMPOTENCY_KEY_BYTES = 512


@dataclass(frozen=True, slots=True)
class IdempotencyIdentity:
    key_digest: str
    request_digest: str


@dataclass(frozen=True, slots=True)
class IdempotencyScope:
    actor: AuthenticatedActor
    organization_id: str
    workspace_id: str
    operation: str
    resource_scope_id: str
    identity: IdempotencyIdentity


@dataclass(frozen=True, slots=True)
class ReplayResult[Result: BaseModel]:
    result: Result
    status_code: int


def idempotency_identity(key: str, request: bytes | BaseModel) -> IdempotencyIdentity:
    try:
        encoded_key = key.encode("ascii")
    except UnicodeEncodeError as error:
        raise _invalid_idempotency_key() from error
    if (
        not encoded_key
        or len(encoded_key) > _MAX_IDEMPOTENCY_KEY_BYTES
        or any(byte < 0x21 or byte > 0x7E for byte in encoded_key)
    ):
        raise _invalid_idempotency_key()
    request_bytes = request if isinstance(request, bytes) else _canonical_json(request.model_dump(mode="json"))
    return IdempotencyIdentity(
        key_digest=hashlib.sha256(encoded_key).hexdigest(),
        request_digest=hashlib.sha256(request_bytes).hexdigest(),
    )


async def load_replay[ResponseModel: BaseModel](
    session: AsyncSession,
    *,
    scope: IdempotencyScope,
    now: datetime,
    response_model: type[ResponseModel],
) -> ReplayResult[ResponseModel] | None:
    record = await session.scalar(
        select(SkillIdempotencyRecord).where(
            SkillIdempotencyRecord.organization_id == scope.organization_id,
            SkillIdempotencyRecord.workspace_id == scope.workspace_id,
            SkillIdempotencyRecord.actor_type == scope.actor.principal.principal_type.value,
            SkillIdempotencyRecord.actor_id == scope.actor.principal.principal_id,
            SkillIdempotencyRecord.operation == scope.operation,
            SkillIdempotencyRecord.scope_id == scope.resource_scope_id,
            SkillIdempotencyRecord.key_digest == scope.identity.key_digest,
        )
    )
    if record is None:
        return None
    if _as_utc(record.expires_at) <= _as_utc(now):
        await session.delete(record)
        await session.flush()
        return None
    if record.request_digest != scope.identity.request_digest:
        raise SkillManagementError(
            "idempotency_conflict",
            "The Idempotency-Key was already used with different request content.",
            status_code=409,
        )
    return ReplayResult(
        result=response_model.model_validate(record.response_body),
        status_code=record.status_code,
    )


def new_replay_evidence(
    *,
    scope: IdempotencyScope,
    response: BaseModel,
    status_code: int,
    now: datetime,
) -> SkillIdempotencyRecord:
    return SkillIdempotencyRecord(
        id=new_object_id("sidm"),
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        actor_type=scope.actor.principal.principal_type.value,
        actor_id=scope.actor.principal.principal_id,
        operation=scope.operation,
        scope_id=scope.resource_scope_id,
        key_digest=scope.identity.key_digest,
        request_digest=scope.identity.request_digest,
        response_body=response.model_dump(mode="json"),
        status_code=status_code,
        created_at=now,
        expires_at=now + IDEMPOTENCY_LIFETIME,
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
        raise SkillManagementError(
            concealed_code if error.concealed else "permission_denied",
            "The requested resource was not found." if error.concealed else "Permission denied.",
            status_code=404 if error.concealed else 403,
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
    return SecurityAuditRecord(
        id=new_object_id("aud"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        action=action,
        resource_type="skill",
        resource_id=skill_id,
        auth_method=actor.auth_method,
        credential_id=actor.credential_id,
        outcome=outcome,
        occurred_at=now,
        request_id=actor.request_id,
        details=details,
    )


def is_idempotency_race(error: IntegrityError) -> bool:
    """Return whether an insert lost the unique replay-scope race."""

    constraint_name = getattr(getattr(error, "orig", None), "diag", None)
    if constraint_name is not None:
        return getattr(constraint_name, "constraint_name", None) == "uq_skill_idempotency_replay_scope"
    message = str(error.orig).casefold()
    return "unique constraint failed" in message and "skill_idempotency.operation" in message


async def record_failed_skill_attempt(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    skill_id: str | None,
    action: str,
    now: datetime,
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
            )
        )


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _invalid_idempotency_key() -> SkillManagementError:
    return SkillManagementError(
        "invalid_request",
        "Idempotency-Key must contain 1 through 512 visible ASCII bytes.",
        status_code=400,
    )


def _as_utc(value: datetime) -> datetime:
    from datetime import UTC

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
