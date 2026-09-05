"""Shared Connectivity management command and canonicalization primitives."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel, JsonValue, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.models import ConnectivityCommandRecord
from a13n_service.durable_operations.idempotency import InvalidIdempotencyKey, digest_visible_ascii_key
from a13n_service.iam.authorization import AuthenticatedActor
from a13n_service.ids import new_object_id


class ConnectivityManagementValueError(ValueError):
    pass


async def replay_command(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str | None,
    operation: str,
    scope_id: str,
    idempotency_key_digest: str,
    fingerprint: str,
) -> ConnectivityCommandRecord | None:
    record = await session.scalar(
        select(ConnectivityCommandRecord).where(
            ConnectivityCommandRecord.workspace_id == workspace_id,
            ConnectivityCommandRecord.actor_type == actor.principal.principal_type.value,
            ConnectivityCommandRecord.actor_id == actor.principal.principal_id,
            ConnectivityCommandRecord.operation == operation,
            ConnectivityCommandRecord.scope_id == scope_id,
            ConnectivityCommandRecord.idempotency_key_digest == idempotency_key_digest,
        )
    )
    if record is not None and record.request_fingerprint != fingerprint:
        raise ConnectivityManagementValueError("idempotency_conflict")
    return record


def record_command(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str | None,
    operation: str,
    scope_id: str,
    idempotency_key_digest: str,
    fingerprint: str,
    resource_type: str,
    resource_id: str,
    result_version: int,
    now: datetime,
    result: dict[str, JsonValue] | None = None,
) -> ConnectivityCommandRecord:
    record = ConnectivityCommandRecord(
        id=new_object_id("idem"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        operation=operation,
        scope_id=scope_id,
        idempotency_key_digest=idempotency_key_digest,
        request_fingerprint=fingerprint,
        resource_type=resource_type,
        resource_id=resource_id,
        result_version=result_version,
        created_at=now,
        result_json=result,
    )
    session.add(record)
    return record


def clear_credentials(value: dict[str, SecretStr]) -> dict[str, str]:
    return {key: secret.get_secret_value() for key, secret in value.items()}


def fingerprint(value: BaseModel, *, credentials: Mapping[str, object] | None = None) -> str:
    payload = value.model_dump(mode="json", exclude={"credentials"})
    if credentials is not None:
        payload["credentials_sha256"] = canonical_digest(credentials)
    return canonical_digest(payload)


def canonical_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def idempotency_key_digest(value: str) -> str:
    try:
        return digest_visible_ascii_key(value)
    except InvalidIdempotencyKey as error:
        raise ConnectivityManagementValueError("invalid_idempotency_key") from error
