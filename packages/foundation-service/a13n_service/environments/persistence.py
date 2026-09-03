"""Environment persistence values shared by transactional service operations."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction

from .access import authorize_environment_workspace, load_environment, load_environment_revision
from .domain import Environment, EnvironmentRevision
from .errors import EnvironmentManagementError
from .models import EnvironmentProviderSelectionRecord, EnvironmentRecord

IDEMPOTENCY_LIFETIME = timedelta(hours=24)
_MAX_IDEMPOTENCY_KEY_BYTES = 512


def request_identity(idempotency_key: str, request: BaseModel) -> tuple[str, str]:
    try:
        encoded = idempotency_key.encode("ascii")
    except UnicodeEncodeError as error:
        raise _invalid_idempotency_key() from error
    if not 1 <= len(encoded) <= _MAX_IDEMPOTENCY_KEY_BYTES or any(byte < 0x21 or byte > 0x7E for byte in encoded):
        raise _invalid_idempotency_key()
    request_digest = hashlib.sha256(request.model_dump_json(by_alias=True, exclude_none=False).encode()).hexdigest()
    return hashlib.sha256(encoded).hexdigest(), request_digest


async def load_replay(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    operation: str,
    scope_id: str,
    identity: tuple[str, str],
    now: datetime,
) -> tuple[str, str] | None:
    key_digest, request_digest = identity
    evidence = await session.scalar(
        select(IdempotencyEvidenceRecord).where(
            IdempotencyEvidenceRecord.workspace_id == actor.boundary_workspace_id,
            IdempotencyEvidenceRecord.actor_type == actor.principal.principal_type.value,
            IdempotencyEvidenceRecord.actor_id == actor.principal.principal_id,
            IdempotencyEvidenceRecord.operation == operation,
            IdempotencyEvidenceRecord.scope_id == scope_id,
            IdempotencyEvidenceRecord.key_digest == key_digest,
            IdempotencyEvidenceRecord.expires_at > now,
        )
    )
    if evidence is None:
        return None
    if evidence.request_digest != request_digest:
        raise EnvironmentManagementError(
            "idempotency_conflict",
            "The Idempotency-Key was already used with different request content.",
            status_code=409,
        )
    return evidence.result_kind, evidence.result_ref


def evidence_record(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    operation: str,
    scope_id: str,
    identity: tuple[str, str],
    result_kind: str,
    result_ref: str,
    now: datetime,
) -> IdempotencyEvidenceRecord:
    return IdempotencyEvidenceRecord(
        id=new_object_id("idem"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        operation=operation,
        scope_id=scope_id,
        key_digest=identity[0],
        request_digest=identity[1],
        result_kind=result_kind,
        result_ref=result_ref,
        created_at=now,
        expires_at=now + IDEMPOTENCY_LIFETIME,
    )


def audit_record(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    now: datetime,
) -> SecurityAuditRecord:
    return SecurityAuditRecord(
        id=new_object_id("audit"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        auth_method=actor.auth_method,
        credential_id=actor.credential_id,
        outcome="success",
        occurred_at=now,
        request_id=actor.request_id,
        details=None,
    )


def normalized_name(value: str) -> str:
    return value.casefold()


def require_etag(
    record: EnvironmentRecord | EnvironmentProviderSelectionRecord,
    if_match: str | None,
    *,
    resource_id: str,
) -> None:
    current = resource_etag(resource_id, record.updated_at)
    if if_match is None or not etag_matches(if_match, current):
        raise precondition_failed(current)


def precondition_failed(current_etag: str | None) -> EnvironmentManagementError:
    details: dict[str, object] = {"current_etag": current_etag} if current_etag is not None else {}
    return EnvironmentManagementError(
        "precondition_failed",
        "The resource changed after it was read.",
        status_code=412,
        details=details,
    )


def is_idempotency_race(error: IntegrityError) -> bool:
    diagnostic = getattr(getattr(error, "orig", None), "diag", None)
    if diagnostic is not None:
        return getattr(diagnostic, "constraint_name", None) == "uq_idempotency_evidence_replay_scope"
    message = str(error).lower()
    return "unique constraint failed" in message and "idempotency_evidence.actor_type" in message


async def replay_environment_create(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    identity: tuple[str, str],
    now: datetime,
) -> Environment:
    async with transaction(sessions) as session:
        workspace = await authorize_environment_workspace(
            session,
            actor=actor,
            workspace_id=workspace_id,
            action=WorkspaceAction.environment_manage,
        )
        replay = await load_replay(
            session,
            actor=actor,
            operation="environment.create",
            scope_id=workspace_id,
            identity=identity,
            now=now,
        )
        if replay is None:
            raise EnvironmentManagementError(
                "write_conflict",
                "The Environment could not be created.",
                status_code=409,
            )
        return (
            await load_environment(
                session,
                organization_id=workspace.organization_id,
                workspace_id=workspace_id,
                environment_id=replay[1],
            )
        ).to_resource()


async def replay_environment_revision_create(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    environment_id: str,
    identity: tuple[str, str],
    now: datetime,
) -> tuple[EnvironmentRevision, bool]:
    async with transaction(sessions) as session:
        workspace = await authorize_environment_workspace(
            session,
            actor=actor,
            workspace_id=actor.boundary_workspace_id,
            action=WorkspaceAction.environment_manage,
        )
        replay = await load_replay(
            session,
            actor=actor,
            operation="environment.revision.create",
            scope_id=environment_id,
            identity=identity,
            now=now,
        )
        if replay is None:
            raise EnvironmentManagementError(
                "write_conflict",
                "The Revision could not be created.",
                status_code=409,
            )
        revision = await load_environment_revision(
            session,
            organization_id=workspace.organization_id,
            workspace_id=workspace.workspace_id,
            revision_id=replay[1],
        )
        return revision.to_resource(), replay[0] == "environment_revision_created"


def _invalid_idempotency_key() -> EnvironmentManagementError:
    return EnvironmentManagementError(
        "invalid_request",
        "Idempotency-Key must contain 1 through 512 visible ASCII bytes.",
        status_code=400,
    )


__all__ = [
    "audit_record",
    "evidence_record",
    "is_idempotency_race",
    "load_replay",
    "normalized_name",
    "precondition_failed",
    "replay_environment_create",
    "replay_environment_revision_create",
    "request_identity",
    "require_etag",
]
