"""Relational MCPConnection, OAuth session, and immutable catalog facts."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base
from a13n_service.iam.domain import PrincipalRef, PrincipalType

from .domain import MCPAuthMode, MCPConnection, MCPConnectionStatus, MCPConnectionStatusReason


class MCPConnectionRecord(Base):
    __tablename__ = "mcp_connections"
    __table_args__ = (
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
        CheckConstraint("catalog_generation >= 0", name="catalog_generation_non_negative"),
        CheckConstraint("catalog_claim_generation >= 0", name="catalog_claim_generation_non_negative"),
        CheckConstraint("cleanup_attempt_count >= 0", name="cleanup_attempt_count_non_negative"),
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="name_bounded"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        Index("uq_mcp_connections_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index("uq_mcp_connections_workspace_name", "workspace_id", "normalized_name", unique=True),
        Index("ix_mcp_connections_workspace_updated", "workspace_id", "updated_at", "id"),
        Index("ix_mcp_connections_owner", "workspace_id", "owner_user_id", "status", "id"),
        Index(
            "ix_mcp_connections_catalog_reconcile", "status", "catalog_available_at", "catalog_claim_expires_at", "id"
        ),
        Index("ix_mcp_connections_cleanup", "cleanup_pending", "cleanup_available_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(String(72), ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    auth_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    static_header_names_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    credential_secret_id: Mapped[str | None] = mapped_column(String(72), ForeignKey("secrets.id", ondelete="RESTRICT"))
    credential_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    catalog_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    catalog_claim_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    catalog_claim_owner: Mapped[str | None] = mapped_column(String(128))
    catalog_claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    catalog_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    catalog_last_error_code: Mapped[str | None] = mapped_column(String(128))
    cleanup_pending: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cleanup_attempt_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cleanup_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cleanup_last_error_code: Mapped[str | None] = mapped_column(String(128))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self, catalog_digest: str | None = None) -> MCPConnection:
        return MCPConnection(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            owner_user_id=self.owner_user_id,
            name=self.name,
            endpoint_url=self.endpoint_url,
            auth_mode=MCPAuthMode(self.auth_mode),
            static_header_names=tuple(self.static_header_names_json),
            status=MCPConnectionStatus(self.status),
            status_reason=MCPConnectionStatusReason(self.status_reason) if self.status_reason else None,
            version=self.version,
            credential_configured=self.credential_secret_id is not None,
            credential_generation=self.credential_generation,
            catalog_digest=catalog_digest,
            created_by=PrincipalRef(
                principal_type=PrincipalType(self.created_by_type),
                principal_id=self.created_by_id,
            ),
            created_at=_utc(self.created_at),
            updated_at=_utc(self.updated_at),
        )


class MCPOAuthSessionRecord(Base):
    __tablename__ = "mcp_oauth_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ("mcp_connection_id", "organization_id", "workspace_id"),
            ("mcp_connections.id", "mcp_connections.organization_id", "mcp_connections.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('pending', 'exchanging', 'completed', 'failed', 'expired')", name="status_valid"),
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
    state_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    issuer_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    authorization_endpoint: Mapped[str] = mapped_column(String(2048), nullable=False)
    token_endpoint: Mapped[str] = mapped_column(String(2048), nullable=False)
    registration_endpoint: Mapped[str | None] = mapped_column(String(2048))
    client_id: Mapped[str] = mapped_column(String(2048), nullable=False)
    scope: Mapped[str | None] = mapped_column(String(2048))
    setup_secret_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("secrets.id", ondelete="RESTRICT"), nullable=False
    )
    setup_secret_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    claim_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claim_owner: Mapped[str | None] = mapped_column(String(128))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MCPToolCatalogRecord(Base):
    __tablename__ = "mcp_tool_catalogs"
    __table_args__ = (
        ForeignKeyConstraint(
            ("mcp_connection_id", "organization_id", "workspace_id"),
            ("mcp_connections.id", "mcp_connections.organization_id", "mcp_connections.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        CheckConstraint("tool_count >= 0", name="tool_count_non_negative"),
        CheckConstraint("credential_generation >= 0", name="credential_generation_non_negative"),
        CheckConstraint("catalog_generation >= 1", name="catalog_generation_positive"),
        Index("uq_mcp_tool_catalogs_digest", "mcp_connection_id", "digest_sha256", unique=True),
        Index("ix_mcp_tool_catalogs_latest", "mcp_connection_id", "published_at", "id"),
        Index("ix_mcp_tool_catalogs_retention", "retain_until", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    mcp_connection_id: Mapped[str] = mapped_column(String(72), nullable=False)
    digest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tool_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    credential_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    catalog_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    protocol_revision: Mapped[str] = mapped_column(String(32), nullable=False)
    server_name: Mapped[str] = mapped_column(String(128), nullable=False)
    server_version: Mapped[str] = mapped_column(String(128), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retain_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
