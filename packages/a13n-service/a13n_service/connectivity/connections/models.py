"""One connection identity and lifecycle, with protocol-specific bindings."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from a13n_service.credentials import ResourceCredential
from a13n_service.database import Base
from a13n_service.names import CASEFOLDED_NAME_MAX_LENGTH


class ConnectionRecord(ResourceCredential[str], Base):
    credential_owner_type = "connection"
    __tablename__ = "connections"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"), ("workspaces.id", "workspaces.organization_id"), ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ("connector_provider_id", "organization_id"),
            ("connector_providers.id", "connector_providers.organization_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("kind IN ('connector', 'mcp')", name="kind_valid"),
        CheckConstraint("status IN ('pending', 'ready', 'action_required', 'disabled')", name="status_valid"),
        CheckConstraint("(status = 'action_required') = (status_reason IS NOT NULL)", name="status_reason_valid"),
        CheckConstraint(
            "status_reason IS NULL OR status_reason IN ('reauthorization_required', 'incompatible')",
            name="status_reason_value_valid",
        ),
        CheckConstraint("version >= 1 AND authorization_generation >= 1", name="versions_positive"),
        CheckConstraint(
            "credential_generation >= 0 AND refresh_claim_generation >= 0", name="credential_generations_valid"
        ),
        CheckConstraint("setup_generation >= 1", name="setup_generation_positive"),
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="name_bounded"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        CheckConstraint(
            "(ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL) OR (ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL)",
            name="credential_material_consistent",
        ),
        CheckConstraint(
            "(kind = 'connector' AND connector_provider_id IS NOT NULL AND connector_key IS NOT NULL AND endpoint_url IS NULL AND auth_mode IS NULL) OR (kind = 'mcp' AND endpoint_url IS NOT NULL AND auth_mode IN ('none', 'bearer', 'oauth', 'static_headers') AND connector_provider_id IS NULL AND connector_key IS NULL)",
            name="source_consistent",
        ),
        CheckConstraint(
            "kind != 'connector' OR status != 'ready' OR (external_ref IS NOT NULL AND external_user_correlation IS NOT NULL)",
            name="verified_connector_binding_required",
        ),
        Index("uq_connections_identity", "id", "organization_id", "workspace_id", unique=True),
        Index("uq_connections_workspace_name", "workspace_id", "normalized_name", unique=True),
        Index("uq_connections_external_ref", "connector_provider_id", "external_ref", unique=True),
        Index("ix_connections_workspace_updated", "workspace_id", "updated_at", "id"),
        Index("ix_connections_provider_status", "connector_provider_id", "status", "id"),
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, str]:
        return {"polymorphic_on": "kind", "with_polymorphic": "*"}

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(CASEFOLDED_NAME_MAX_LENGTH), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    authorization_generation: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1", nullable=False)
    setup_generation: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1", nullable=False)
    credential_generation: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_check_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    safe_metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=text("'{}'"), nullable=False
    )

    connector_provider_id: Mapped[str] = mapped_column(String(72), nullable=True)
    connector_key: Mapped[str] = mapped_column(String(128), nullable=True)
    external_user_correlation: Mapped[str | None] = mapped_column(String(128))
    external_ref: Mapped[str | None] = mapped_column(String(2048))

    endpoint_url: Mapped[str] = mapped_column(String(2048), nullable=True)
    auth_mode: Mapped[str] = mapped_column(String(32), nullable=True)
    static_header_names_json: Mapped[list[str]] = mapped_column(
        JSON, default=list, server_default=text("'[]'"), nullable=False
    )
    refresh_claim_generation: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    refresh_claim_owner: Mapped[str | None] = mapped_column(String(128))
    refresh_claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def to_resource(self):
        from .access import project

        return project(self)


class AuthorizationRecord(ResourceCredential[str], Base):
    """A short-lived authorization operation; protocol material is encrypted."""

    credential_owner_type = "connection_authorization"
    __tablename__ = "connection_authorizations"
    __table_args__ = (
        ForeignKeyConstraint(
            ("connection_id", "organization_id", "workspace_id"),
            ("connections.id", "connections.organization_id", "connections.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("kind IN ('connector', 'mcp')", name="kind_valid"),
        CheckConstraint(
            "status IN ('pending', 'starting', 'attached', 'received', 'reserved', 'exchanging', 'completed', 'failed', 'expired', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint("initiating_principal_type IN ('user', 'service_account')", name="initiator_valid"),
        CheckConstraint("generation >= 1 AND connection_version >= 1", name="versions_positive"),
        CheckConstraint("credential_generation >= 0 AND claim_generation >= 0", name="claim_generations_valid"),
        CheckConstraint(
            "completion_method IN ('polling', 'oauth_verifier', 'browser_confirmation', 'mcp_oauth', 'credentials')",
            name="completion_method_valid",
        ),
        CheckConstraint(
            "(ciphertext IS NULL AND nonce IS NULL AND encryption_key_id IS NULL) OR (ciphertext IS NOT NULL AND nonce IS NOT NULL AND encryption_key_id IS NOT NULL)",
            name="credential_material_consistent",
        ),
        Index("uq_connection_authorizations_generation", "connection_id", "generation", unique=True),
        Index("uq_connection_authorizations_state", "state_digest", unique=True),
        Index("ix_connection_authorizations_reconcile", "kind", "status", "available_at", "claim_expires_at", "id"),
        Index("ix_connection_authorizations_expiry", "expires_at", "status", "id"),
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, str]:
        return {"polymorphic_on": "kind", "with_polymorphic": "*"}

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    connection_id: Mapped[str] = mapped_column(String(72), nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1", nullable=False)
    connection_version: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1", nullable=False)
    initiating_principal_type: Mapped[str] = mapped_column(
        String(32), default="user", server_default="user", nullable=False
    )
    initiating_principal_id: Mapped[str] = mapped_column(String(72), nullable=False)
    completion_method: Mapped[str] = mapped_column(
        String(32), default="polling", server_default="polling", nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    credential_generation: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    return_url: Mapped[str | None] = mapped_column(String(2048))
    client_state: Mapped[str | None] = mapped_column(String(512))
    completion_challenge: Mapped[str | None] = mapped_column(String(64))
    launch_token_digest: Mapped[str | None] = mapped_column(String(64))
    browser_binding_digest: Mapped[str | None] = mapped_column(String(64))
    receipt_digest: Mapped[str | None] = mapped_column(String(64))
    state_digest: Mapped[str | None] = mapped_column(String(64))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    claim_generation: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0", nullable=False)
    claim_owner: Mapped[str | None] = mapped_column(String(128))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    type: Mapped[str] = mapped_column(String(64), nullable=True)
    connector_key: Mapped[str] = mapped_column(String(128), nullable=True)
    external_user_correlation: Mapped[str] = mapped_column(String(128), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(2048))
    setup_ref: Mapped[str | None] = mapped_column(String(2048))
    setup_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default=text("'{}'"), nullable=False)

    @property
    def credential_owner_id(self) -> str:
        return self.connection_id
