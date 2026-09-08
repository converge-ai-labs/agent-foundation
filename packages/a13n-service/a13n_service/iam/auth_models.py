"""Credential verifiers and invitations; plaintext credentials are never persisted."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base


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
