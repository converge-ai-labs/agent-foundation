"""Canonical construction of durable security-audit events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .authorization import AuthenticatedActor
from .models import SecurityAuditRecord


@dataclass(frozen=True, slots=True)
class SystemAuditActor:
    """The fixed actor shape for an internally completed operation."""

    request_id: str | None


def security_audit_record(
    *,
    audit_id: str,
    actor: AuthenticatedActor | SystemAuditActor,
    organization_id: str | None,
    workspace_id: str | None,
    action: str,
    resource_type: str | None,
    resource_id: str | None,
    outcome: str,
    occurred_at: datetime,
    details: dict[str, object] | None,
) -> SecurityAuditRecord:
    """Build an audit row without changing caller-owned policy or time semantics."""

    if isinstance(actor, SystemAuditActor):
        actor_type = "system"
        actor_id = None
        auth_method = "internal"
        credential_id = None
        request_id = actor.request_id
    else:
        actor_type = actor.principal.principal_type.value
        actor_id = actor.principal.principal_id
        auth_method = actor.auth_method
        credential_id = actor.credential_id
        request_id = actor.request_id
    return SecurityAuditRecord(
        id=audit_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        auth_method=auth_method,
        credential_id=credential_id,
        outcome=outcome,
        occurred_at=occurred_at,
        request_id=request_id,
        details=details,
    )


__all__ = ["SystemAuditActor", "security_audit_record"]
