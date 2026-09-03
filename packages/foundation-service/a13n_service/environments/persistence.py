"""Environment persistence values shared by transactional service operations."""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.durable_operations.idempotency import (
    EvidenceScope,
    IdempotencyConflict,
    IdempotencyIdentity,
    InvalidIdempotencyKey,
    digest_visible_ascii_key,
    load_evidence,
    new_evidence,
)
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.etags import etag_matches, resource_etag
from a13n_service.iam import AuthenticatedActor
from a13n_service.iam.audit import security_audit_record
from a13n_service.iam.authorization import WorkspaceAction
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id
from a13n_service.storage import transaction

from .access import authorize_environment_workspace, load_environment, load_environment_revision
from .domain import Environment, EnvironmentRevision
from .errors import EnvironmentManagementError
from .models import EnvironmentProviderSelectionRecord, EnvironmentRecord


def request_identity(idempotency_key: str, request: BaseModel) -> IdempotencyIdentity:
    try:
        key_digest = digest_visible_ascii_key(idempotency_key)
    except InvalidIdempotencyKey as error:
        raise _invalid_idempotency_key() from error
    request_digest = hashlib.sha256(request.model_dump_json(by_alias=True, exclude_none=False).encode()).hexdigest()
    return IdempotencyIdentity(key_digest, request_digest)


async def load_replay(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    operation: str,
    scope_id: str,
    identity: IdempotencyIdentity,
    now: datetime,
) -> tuple[str, str] | None:
    try:
        evidence = await load_evidence(
            session,
            scope=EvidenceScope(
                workspace_id=actor.boundary_workspace_id,
                actor_type=actor.principal.principal_type.value,
                actor_id=actor.principal.principal_id,
                operation=operation,
                scope_id=scope_id,
            ),
            identity=identity,
            now=now,
        )
    except IdempotencyConflict as error:
        raise EnvironmentManagementError(
            "idempotency_conflict",
            "The Idempotency-Key was already used with different request content.",
            status_code=409,
        ) from error
    if evidence is None:
        return None
    return evidence.result_kind, evidence.result_ref


def evidence_record(
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    operation: str,
    scope_id: str,
    identity: IdempotencyIdentity,
    result_kind: str,
    result_ref: str,
    now: datetime,
) -> IdempotencyEvidenceRecord:
    return new_evidence(
        organization_id=organization_id,
        scope=EvidenceScope(
            workspace_id=workspace_id,
            actor_type=actor.principal.principal_type.value,
            actor_id=actor.principal.principal_id,
            operation=operation,
            scope_id=scope_id,
        ),
        identity=identity,
        result_kind=result_kind,
        result_ref=result_ref,
        now=now,
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
    return security_audit_record(
        audit_id=new_object_id("audit"),
        actor=actor,
        organization_id=organization_id,
        workspace_id=workspace_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome="success",
        occurred_at=now,
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


async def replay_environment_create(
    sessions: async_sessionmaker[AsyncSession],
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
    identity: IdempotencyIdentity,
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
    identity: IdempotencyIdentity,
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
    "load_replay",
    "normalized_name",
    "precondition_failed",
    "replay_environment_create",
    "replay_environment_revision_create",
    "request_identity",
    "require_etag",
]
