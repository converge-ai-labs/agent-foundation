"""Append-only, bounded audit evidence, written with the owning mutation."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Literal, Protocol

from pydantic import JsonValue
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, immutable, rules
from a13n_service.infra.ids import new_object_id

# User accounts and their login sessions are global; every other target belongs to an organization.
_ACCOUNT_TARGETS = frozenset({"user", "login_session"})


_origin: ContextVar[dict[str, JsonValue] | None] = ContextVar("platform_operation_origin", default=None)


@contextmanager
def operation_origin(run_id: str, attempt_id: str, request_id: str | None) -> Iterator[None]:
    marker = _origin.set({"run_id": run_id, "attempt_id": attempt_id, "request_id": request_id})
    try:
        yield
    finally:
        _origin.reset(marker)


class Scoped(Protocol):
    """The tenant an audited target belongs to: a `Scope`, a `WorkspaceScope` or a view carrying both IDs."""

    @property
    def organization_id(self) -> str: ...

    @property
    def workspace_id(self) -> str | None: ...


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("outcome IN ('ok', 'denied', 'failed')", name="outcome"),
        CheckConstraint("organization_id IS NOT NULL OR workspace_id IS NULL", name="global_scope"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        Index("ix_audit_events_organization", "organization_id", "occurred_at", "id"),
        Index("ix_audit_events_workspace", "workspace_id", "occurred_at", "id"),
        # A user's own trail: what they did anywhere, and what was done to their account.
        Index("ix_audit_events_actor", "actor_id", "occurred_at", "id"),
        Index("ix_audit_events_target", "target_id", "occurred_at", "id"),
        rules(immutable("audit_events")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"))
    workspace_id: Mapped[str | None]
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("principals.id"))
    action: Mapped[str]
    target_kind: Mapped[str]
    target_id: Mapped[str]
    outcome: Mapped[str]
    details: Mapped[dict] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def record(
    session: AsyncSession,
    scope: Scoped | None,
    *,
    actor_id: str | None,
    action: str,
    target_kind: str,
    target_id: str,
    outcome: Literal["ok", "denied", "failed"] = "ok",
    details: dict[str, JsonValue] | None = None,
) -> None:
    """Append to the caller's mutation transaction in the target's actual scope; account-wide targets have none."""
    if (scope is None) != (target_kind in _ACCOUNT_TARGETS):
        raise ValueError("Exactly the account-wide audit targets omit tenant scope")
    payload = dict(details or {})
    if origin := _origin.get():
        payload["execution_origin"] = origin
    if len(json.dumps(payload, allow_nan=False).encode()) > 8192:
        raise ValueError("Audit details exceed their byte limit")
    session.add(
        AuditEventRow(
            id=new_object_id("audit"),
            organization_id=scope.organization_id if scope is not None else None,
            workspace_id=scope.workspace_id if scope is not None else None,
            actor_id=actor_id,
            action=action,
            target_kind=target_kind,
            target_id=target_id,
            outcome=outcome,
            details=payload,
        )
    )
