"""Shared Connectivity management command and canonicalization primitives."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, JsonValue, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.digests import digest_request
from a13n_service.durable_operations.idempotency import (
    EvidenceScope,
    IdempotencyIdentity,
    load_evidence,
    new_evidence,
)
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.iam.authorization import AuthenticatedActor


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    resource_id: str
    result_version: int
    created_at: datetime
    resource: dict[str, JsonValue] | None

    def restore[T: BaseModel](self, model: type[T]) -> T:
        if self.resource is None:
            raise RuntimeError("command receipt has no resource projection")
        return model.model_validate(self.resource)


async def replay_command(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str | None,
    operation: str,
    scope_id: str,
    idempotency_key_digest: str,
    fingerprint: str,
    now: datetime,
) -> CommandReceipt | None:
    evidence = await load_evidence(
        session,
        scope=EvidenceScope(
            workspace_id,
            actor.principal.principal_type.value,
            actor.principal.principal_id,
            operation,
            scope_id,
            organization_id=actor.boundary_organization_id,
        ),
        identity=IdempotencyIdentity(idempotency_key_digest, fingerprint),
        now=now,
    )
    if evidence is None:
        return None
    payload = evidence.receipt_json
    if payload is None or not isinstance(payload.get("version"), int):
        raise RuntimeError("command evidence is missing its receipt")
    version = payload["version"]
    assert isinstance(version, int)
    resource = payload.get("resource")
    return CommandReceipt(
        evidence.result_ref, version, evidence.created_at, resource if isinstance(resource, dict) else None
    )


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
    resource: BaseModel | None,
) -> IdempotencyEvidenceRecord:
    record = new_evidence(
        organization_id=organization_id,
        scope=EvidenceScope(
            workspace_id,
            actor.principal.principal_type.value,
            actor.principal.principal_id,
            operation,
            scope_id,
            organization_id=actor.boundary_organization_id,
        ),
        identity=IdempotencyIdentity(idempotency_key_digest, fingerprint),
        result_kind=resource_type,
        result_ref=resource_id,
        now=now,
        receipt={
            "version": result_version,
            "resource": None if resource is None else resource.model_dump(mode="json", by_alias=True),
        },
    )

    session.add(record)
    return record


def clear_credentials(value: dict[str, SecretStr]) -> dict[str, str]:
    return {key: secret.get_secret_value() for key, secret in value.items()}


def fingerprint(value: BaseModel, *, credentials: Mapping[str, object] | None = None) -> str:
    payload = value.model_dump(mode="json", exclude={"credentials"})
    if credentials is not None:
        payload["credentials_sha256"] = digest_request(credentials)
    return digest_request(payload)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
