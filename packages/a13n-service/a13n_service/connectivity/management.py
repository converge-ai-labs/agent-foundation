"""Shared Connectivity management command and canonicalization primitives."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.durable_operations.idempotency import IdempotencyIdentity
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.durable_operations.requests import evidence_record, load_receipt, request_scope
from a13n_service.iam.authorization import AuthenticatedActor


@dataclass(frozen=True, slots=True)
class CommandReference:
    resource_type: str
    resource_id: str


async def replay_command(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str | None,
    operation: str,
    scope_id: str,
    idempotency_key_digest: str,
    now: datetime,
) -> CommandReference | None:
    receipt = await load_receipt(
        session,
        scope=request_scope(actor, workspace_id=workspace_id, operation=operation, scope_id=scope_id),
        identity=IdempotencyIdentity(idempotency_key_digest),
        now=now,
    )
    if receipt is None:
        return None
    return CommandReference(receipt.result_kind, receipt.result_ref)


def record_command(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str | None,
    operation: str,
    scope_id: str,
    idempotency_key_digest: str,
    resource_type: str,
    resource_id: str,
    now: datetime,
) -> IdempotencyEvidenceRecord:
    record = evidence_record(
        actor=actor,
        organization_id=organization_id,
        workspace_id=workspace_id,
        operation=operation,
        scope_id=scope_id,
        identity=IdempotencyIdentity(idempotency_key_digest),
        result_kind=resource_type,
        result_ref=resource_id,
        now=now,
    )

    session.add(record)
    return record


def clear_credentials(value: dict[str, SecretStr]) -> dict[str, str]:
    return {key: secret.get_secret_value() for key, secret in value.items()}


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
