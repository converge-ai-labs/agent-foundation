"""Shared persistence and validation primitives for Ingress management."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime

from pydantic import BaseModel, SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.connectivity.adapters import IngressAdapter
from a13n_service.connectivity.composition import AdapterRegistry
from a13n_service.connectivity.models import ConnectivityCommandRecord
from a13n_service.iam.authorization import (
    AuthenticatedActor,
    AuthorizationError,
    WorkspaceAction,
    authorize_workspace,
)
from a13n_service.iam.models import SecurityAuditRecord
from a13n_service.ids import new_object_id
from a13n_service.secrets import SecretOperation, SecretOwnerType, SecretUseContext

from .errors import IngressError
from .models import IngressAgentRecord, IngressRecord, RouteRecord


def require_adapter(
    adapters: AdapterRegistry[IngressAdapter], provider_key: str, config_version: str
) -> IngressAdapter:
    try:
        return adapters.create(provider_key, config_version=config_version)
    except ValueError as error:
        raise IngressError(
            "unsupported_ingress_adapter", "Ingress adapter is not registered.", status_code=400
        ) from error


async def authorize(
    session: AsyncSession,
    actor: AuthenticatedActor,
    workspace_id: str,
    action: WorkspaceAction,
):
    try:
        return await authorize_workspace(session, actor=actor, workspace_id=workspace_id, action=action)
    except AuthorizationError as error:
        raise IngressError("resource_not_found", "The requested resource was not found.", status_code=404) from error


async def require_ingress(session: AsyncSession, ingress_id: str, *, lock: bool = False) -> IngressRecord:
    query = select(IngressRecord).where(IngressRecord.id == ingress_id)
    if lock:
        query = query.with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise IngressError("resource_not_found", "The requested resource was not found.", status_code=404)
    return record


async def require_route(session: AsyncSession, route_id: str, *, lock: bool = False) -> RouteRecord:
    query = select(RouteRecord).where(RouteRecord.id == route_id)
    if lock:
        query = query.with_for_update()
    record = await session.scalar(query)
    if record is None:
        raise IngressError("resource_not_found", "The requested resource was not found.", status_code=404)
    return record


async def ingress_agent_ids(session: AsyncSession, ingress_id: str) -> tuple[str, ...]:
    return tuple(
        (
            await session.scalars(
                select(IngressAgentRecord.agent_id)
                .where(IngressAgentRecord.ingress_id == ingress_id)
                .order_by(IngressAgentRecord.agent_id)
            )
        ).all()
    )


async def require_routes_fit_agents(session: AsyncSession, ingress_id: str, allowed: frozenset[str]) -> None:
    routes = tuple((await session.scalars(select(RouteRecord).where(RouteRecord.ingress_id == ingress_id))).all())
    for route in routes:
        selected = set(route.capability_overlays_json)
        if route.agent_id is not None:
            selected.add(route.agent_id)
        if not selected <= allowed:
            raise IngressError("invalid_agent_selection", "Existing Routes require removed Agents.", status_code=409)


async def require_non_overlapping(
    session: AsyncSession,
    adapter: IngressAdapter,
    ingress_id: str,
    candidate: dict[str, object],
    *,
    exclude_route_id: str | None = None,
) -> None:
    query = select(RouteRecord).where(RouteRecord.ingress_id == ingress_id, RouteRecord.enabled.is_(True))
    if exclude_route_id is not None:
        query = query.where(RouteRecord.id != exclude_route_id)
    for existing in (await session.scalars(query)).all():
        decision = adapter.prove_non_overlap(existing.match_json, candidate)
        if decision is False:
            raise IngressError("route_overlap", "Route match overlaps an enabled Route.", status_code=409)
        if decision is None and not adapter.allows_runtime_ambiguity:
            raise IngressError(
                "route_overlap_unknown",
                "Route match cannot be proven disjoint from an enabled Route.",
                status_code=409,
            )


async def replay_command(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    workspace_id: str,
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
        raise IngressError("idempotency_conflict", "Idempotency key was used for another request.", status_code=409)
    return record


def record_command(
    session: AsyncSession,
    *,
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    operation: str,
    scope_id: str,
    idempotency_key_digest: str,
    fingerprint: str,
    resource_type: str,
    resource_id: str,
    result_version: int,
    now: datetime,
) -> None:
    session.add(
        ConnectivityCommandRecord(
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
        )
    )


def secret_context(
    *,
    organization_id: str,
    workspace_id: str,
    ingress_id: str,
    generation: int,
    operation: SecretOperation = SecretOperation.management,
) -> SecretUseContext:
    return SecretUseContext(
        organization_id=organization_id,
        workspace_id=workspace_id,
        owner_type=SecretOwnerType.ingress,
        owner_id=ingress_id,
        key="credential_bundle",
        operation=operation,
        credential_generation=generation,
    )


def require_version(current: int, expected: int) -> None:
    if current != expected:
        raise IngressError("version_conflict", "Resource version has changed.", status_code=409)


def require_limit(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise IngressError("invalid_request", "Collection limit must be between 1 and 100.", status_code=400)


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
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise IngressError("invalid_request", "Idempotency-Key is invalid.", status_code=400) from error
    if not 1 <= len(encoded) <= 512 or any(byte < 0x21 or byte > 0x7E for byte in encoded):
        raise IngressError("invalid_request", "Idempotency-Key is invalid.", status_code=400)
    return hashlib.sha256(encoded).hexdigest()


def audit(
    actor: AuthenticatedActor,
    organization_id: str,
    workspace_id: str,
    action: str,
    resource_id: str,
    now: datetime,
) -> SecurityAuditRecord:
    return SecurityAuditRecord(
        id=new_object_id("aud"),
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor.principal.principal_type.value,
        actor_id=actor.principal.principal_id,
        action=action,
        resource_type="ingress" if action.startswith("ingress.") else "route",
        resource_id=resource_id,
        auth_method=actor.auth_method,
        credential_id=actor.credential_id,
        outcome="success",
        occurred_at=now.astimezone(UTC),
        request_id=actor.request_id,
        details=None,
    )
