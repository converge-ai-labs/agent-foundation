"""Canonical IAM rows required by Workspace-owned Service domains."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.names import CASEFOLDED_NAME_MAX_LENGTH


class OrganizationRecord(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    image_id: Mapped[str | None] = mapped_column(String(72))
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
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
    image_id: Mapped[str | None] = mapped_column(String(72))
    name: Mapped[str] = mapped_column(String(128))
    normalized_name: Mapped[str] = mapped_column(String(CASEFOLDED_NAME_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("status IN ('active', 'disabled')", name="status_valid"),)

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    email: Mapped[str] = mapped_column(String(320))
    normalized_email: Mapped[str] = mapped_column(String(320), unique=True)
    image_id: Mapped[str | None] = mapped_column(String(72))
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    normalized_name: Mapped[str] = mapped_column(String(CASEFOLDED_NAME_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(String(2048))
    status: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RoleBindingRecord(Base):
    __tablename__ = "role_bindings"
    __table_args__ = (
        CheckConstraint("principal_type IN ('user', 'service_account')", name="principal_type_valid"),
        CheckConstraint(
            "resource_type IN ('organization', 'workspace', 'agent')",
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


class PasswordCredentialRecord(Base):
    __tablename__ = "password_credentials"

    user_id: Mapped[str] = mapped_column(String(72), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuthSessionRecord(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("ix_auth_sessions_user_created", "user_id", "created_at", "id"),)

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(72), ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKeyRecord(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        ForeignKeyConstraint(
            ("boundary_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("boundary_type = 'workspace'", name="boundary_type_valid"),
        CheckConstraint("principal_type IN ('user', 'service_account')", name="principal_type_valid"),
        Index("ix_api_keys_owner_boundary", "principal_type", "principal_id", "boundary_id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    principal_type: Mapped[str] = mapped_column(String(32))
    principal_id: Mapped[str] = mapped_column(String(72))
    organization_id: Mapped[str] = mapped_column(String(72))
    boundary_type: Mapped[str] = mapped_column(String(32))
    boundary_id: Mapped[str] = mapped_column(String(72))
    name: Mapped[str] = mapped_column(String(128))
    secret_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InvitationRecord(Base):
    __tablename__ = "invitations"
    __table_args__ = (
        Index("uq_invitations_id_organization", "id", "organization_id", unique=True),
        Index(
            "uq_invitations_bootstrap",
            "organization_id",
            unique=True,
            postgresql_where=text("created_by_user_id IS NULL"),
            sqlite_where=text("created_by_user_id IS NULL"),
        ),
        CheckConstraint("verification_mode IN ('email', 'out_of_band')", name="verification_mode_valid"),
        Index("ix_invitations_organization_created", "organization_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), ForeignKey("organizations.id", ondelete="RESTRICT"))
    email: Mapped[str] = mapped_column(String(320))
    normalized_email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    verification_mode: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_user_id: Mapped[str | None] = mapped_column(String(72), ForeignKey("users.id", ondelete="RESTRICT"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[str | None] = mapped_column(String(72), ForeignKey("users.id", ondelete="RESTRICT"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InvitationGrantRecord(Base):
    __tablename__ = "invitation_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ("invitation_id", "organization_id"),
            ("invitations.id", "invitations.organization_id"),
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(resource_type = 'organization' AND workspace_id IS NULL AND resource_id = organization_id "
            "AND role_key IN ('member', 'admin')) OR "
            "(resource_type = 'workspace' AND workspace_id IS NOT NULL AND resource_id = workspace_id "
            "AND role_key IN ('viewer', 'runner', 'builder', 'admin'))",
            name="grant_valid",
        ),
    )

    invitation_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str | None] = mapped_column(String(72))
    resource_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    role_key: Mapped[str] = mapped_column(String(32))


class PasswordResetRecord(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(72), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailChangeRecord(Base):
    __tablename__ = "email_change_tokens"

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(72), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    previous_email: Mapped[str] = mapped_column(String(320))
    new_email: Mapped[str] = mapped_column(String(320))
    new_normalized_email: Mapped[str] = mapped_column(String(320))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
