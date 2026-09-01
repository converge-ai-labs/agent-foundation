"""Canonical IAM rows required by Workspace-owned Foundation domains."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base


class OrganizationRecord(Base):
    __tablename__ = "organizations"
    __table_args__ = (CheckConstraint("version >= 1", name="version_positive"),)

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_workspaces_active_organization_normalized_name",
            "organization_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index("uq_workspaces_id_organization_id", "id", "organization_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), ForeignKey("organizations.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="status_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    email: Mapped[str] = mapped_column(String(320))
    normalized_email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ServiceAccountRecord(Base):
    __tablename__ = "service_accounts"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("status IN ('active', 'disabled')", name="status_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "uq_service_accounts_active_workspace_normalized_name",
            "workspace_id",
            "normalized_name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoleBindingRecord(Base):
    __tablename__ = "role_bindings"
    __table_args__ = (
        CheckConstraint("principal_type IN ('user', 'service_account')", name="principal_type_valid"),
        CheckConstraint(
            "resource_type IN ('organization', 'workspace', 'agent_preset')",
            name="resource_type_valid",
        ),
        CheckConstraint("role_key IN ('member', 'viewer', 'runner', 'builder', 'admin')", name="role_key_valid"),
        Index(
            "uq_role_bindings_principal_resource",
            "principal_type",
            "principal_id",
            "resource_type",
            "resource_id",
            unique=True,
        ),
        Index("ix_role_bindings_authorization", "organization_id", "workspace_id", "principal_type", "principal_id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), ForeignKey("organizations.id", ondelete="CASCADE"))
    workspace_id: Mapped[str | None] = mapped_column(String(72), ForeignKey("workspaces.id", ondelete="CASCADE"))
    principal_type: Mapped[str] = mapped_column(String(32))
    principal_id: Mapped[str] = mapped_column(String(72))
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str] = mapped_column(String(72))
    role_key: Mapped[str] = mapped_column(String(32))
    created_by_user_id: Mapped[str] = mapped_column(String(72), ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SecurityAuditRecord(Base):
    __tablename__ = "security_audit_events"
    __table_args__ = (
        CheckConstraint("actor_type IN ('anonymous', 'user', 'service_account', 'system')", name="actor_type_valid"),
        CheckConstraint("outcome IN ('success', 'failure')", name="outcome_valid"),
        Index("ix_security_audit_workspace_time", "organization_id", "workspace_id", "occurred_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str | None] = mapped_column(String(72))
    workspace_id: Mapped[str | None] = mapped_column(String(72))
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str | None] = mapped_column(String(72))
    action: Mapped[str] = mapped_column(String(128))
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(72))
    auth_method: Mapped[str] = mapped_column(String(32))
    credential_id: Mapped[str | None] = mapped_column(String(72))
    outcome: Mapped[str] = mapped_column(String(16))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    request_id: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict[str, object] | None] = mapped_column(JSON)
