"""Relational MCPConnection, OAuth session, and token-refresh coordination."""

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
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.credentials import ResourceCredential
from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType
from a13n_service.temporal import assume_utc

from .domain import MCPAuthMode, MCPConnection, MCPConnectionStatus, MCPConnectionStatusReason


class MCPConnectionRecord(ResourceCredential, Base):
    credential_owner_type = "mcp_connection"
    __tablename__ = "mcp_connections"
    __table_args__ = (
        CheckConstraint(
            "(ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL) OR "
            "(ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL)",
            name="credential_material_consistent",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("auth_mode IN ('none', 'bearer', 'oauth', 'static_headers')", name="auth_mode_valid"),
        CheckConstraint("status IN ('pending', 'ready', 'action_required', 'disabled')", name="status_valid"),
        CheckConstraint("(status = 'action_required') = (status_reason IS NOT NULL)", name="status_reason_valid"),
        CheckConstraint(
            "status_reason IS NULL OR status_reason IN ('reauthorization_required', 'incompatible')",
            name="status_reason_value_valid",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("credential_generation >= 0", name="credential_generation_non_negative"),
        CheckConstraint("refresh_claim_generation >= 0", name="refresh_claim_generation_non_negative"),
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="name_bounded"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        Index("uq_mcp_connections_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index("uq_mcp_connections_workspace_name", "workspace_id", "normalized_name", unique=True),
        Index("ix_mcp_connections_workspace_updated", "workspace_id", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    auth_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    static_header_names_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    refresh_claim_generation: Mapped[int] = mapped_column(BigInteger, server_default="0", nullable=False)
    refresh_claim_owner: Mapped[str | None] = mapped_column(String(128))
    refresh_claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> MCPConnection:
        return MCPConnection(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            name=self.name,
            endpoint_url=self.endpoint_url,
            auth_mode=MCPAuthMode(self.auth_mode),
            static_header_names=tuple(self.static_header_names_json),
            status=MCPConnectionStatus(self.status),
            status_reason=MCPConnectionStatusReason(self.status_reason) if self.status_reason else None,
            version=self.version,
            credential_configured=self.ciphertext is not None,
            credential_generation=self.credential_generation,
            created_by=PrincipalRef(
                principal_type=PrincipalType(self.created_by_type),
                principal_id=self.created_by_id,
            ),
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


class MCPOAuthSessionRecord(ResourceCredential, Base):
    credential_owner_type = "mcp_oauth_session"
    __tablename__ = "mcp_oauth_sessions"
    __table_args__ = (
        CheckConstraint(
            "(ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL) OR "
            "(ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL)",
            name="credential_material_consistent",
        ),
        ForeignKeyConstraint(
            ("mcp_connection_id", "organization_id", "workspace_id"),
            ("mcp_connections.id", "mcp_connections.organization_id", "mcp_connections.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('pending', 'exchanging', 'completed', 'failed', 'expired')", name="status_valid"),
        CheckConstraint("credential_generation >= 1", name="credential_generation_positive"),
        CheckConstraint("claim_generation >= 0", name="claim_generation_non_negative"),
        Index("uq_mcp_oauth_sessions_state", "state_digest", unique=True),
        Index("ix_mcp_oauth_sessions_connection", "mcp_connection_id", "status", "created_at", "id"),
        Index("ix_mcp_oauth_sessions_expiry", "status", "expires_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    mcp_connection_id: Mapped[str] = mapped_column(String(72), nullable=False)
    initiating_user_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    connection_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    claim_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claim_owner: Mapped[str | None] = mapped_column(String(128))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @property
    def credential_owner_id(self) -> str:
        return self.mcp_connection_id
