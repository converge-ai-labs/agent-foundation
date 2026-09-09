"""Shared Connectivity management command and canonicalization primitives."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, StrictInt
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.digests import digest_request
from a13n_service.durable_operations.idempotency import IdempotencyIdentity
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.durable_operations.requests import evidence_record, load_receipt, request_scope
from a13n_service.iam.authorization import AuthenticatedActor


class CommandReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource_id: str = Field(exclude=True)
    version: StrictInt
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
    receipt = await load_receipt(
        session,
        scope=request_scope(actor, workspace_id=workspace_id, operation=operation, scope_id=scope_id),
        identity=IdempotencyIdentity(idempotency_key_digest, fingerprint),
        now=now,
    )
    if receipt is None:
        return None
    return CommandReceipt.model_validate({**receipt.response, "resource_id": receipt.result_ref})


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
    record = evidence_record(
        actor=actor,
        organization_id=organization_id,
        workspace_id=workspace_id,
        operation=operation,
        scope_id=scope_id,
        identity=IdempotencyIdentity(idempotency_key_digest, fingerprint),
        result_kind=resource_type,
        result_ref=resource_id,
        now=now,
        response=CommandReceipt(
            resource_id=resource_id,
            version=result_version,
            resource=None if resource is None else resource.model_dump(mode="json", by_alias=True),
        ),
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
