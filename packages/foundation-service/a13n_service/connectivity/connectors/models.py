"""Relational ConnectorProvider, ConnectorConnection, setup, operation, and catalog facts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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
from a13n_service.temporal import assume_utc

from .domain import (
    ConnectorConnection,
    ConnectorConnectionStatus,
    ConnectorConnectionStatusReason,
    ConnectorProvider,
    ConnectorProviderStatus,
)


class ConnectorProviderRecord(Base):
    __tablename__ = "connector_providers"
    __table_args__ = (
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="CASCADE",
        ),
        CheckConstraint("status IN ('active', 'disabled')", name="status_valid"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("credential_generation >= 1", name="credential_generation_positive"),
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="name_bounded"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        Index("uq_connector_providers_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index("uq_connector_providers_workspace_name", "workspace_id", "normalized_name", unique=True),
        Index("ix_connector_providers_workspace_updated", "workspace_id", "updated_at", "id"),
        Index("ix_connector_providers_driver_status", "type", "status", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    credential_secret_id: Mapped[str] = mapped_column(
        String(72), ForeignKey("secrets.id", ondelete="RESTRICT"), nullable=False
    )
    credential_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> ConnectorProvider:
        return ConnectorProvider(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            name=self.name,
            type=self.type,
            configuration=self.configuration_json,
            status=ConnectorProviderStatus(self.status),
            version=self.version,
            credential_configured=True,
            credential_generation=self.credential_generation,
            created_by=_principal(self.created_by_type, self.created_by_id),
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


class ConnectorConnectionRecord(Base):
    __tablename__ = "connector_connections"
    __table_args__ = (
        ForeignKeyConstraint(
            ("connector_provider_id", "organization_id", "workspace_id"),
            ("connector_providers.id", "connector_providers.organization_id", "connector_providers.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('pending', 'ready', 'action_required', 'disabled')",
            name="status_valid",
        ),
        CheckConstraint(
            "(status = 'action_required') = (status_reason IS NOT NULL)",
            name="status_reason_valid",
        ),
        CheckConstraint(
            "status IN ('pending', 'disabled') OR external_ref IS NOT NULL",
            name="external_ref_required_when_bound",
        ),
        CheckConstraint("(owner_type IS NULL) = (owner_id IS NULL)", name="owner_complete"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("setup_generation >= 1", name="setup_generation_positive"),
        CheckConstraint("revoke_generation >= 0", name="revoke_generation_non_negative"),
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="name_bounded"),
        CheckConstraint("owner_type IS NULL OR owner_type IN ('user', 'service_account')", name="owner_type_valid"),
        CheckConstraint("created_by_type IN ('user', 'service_account')", name="created_by_type_valid"),
        Index("uq_connector_connections_id_tenant", "id", "organization_id", "workspace_id", unique=True),
        Index(
            "uq_connector_connections_workspace_name",
            "workspace_id",
            "normalized_name",
            unique=True,
        ),
        Index(
            "uq_connector_connections_external_ref",
            "connector_provider_id",
            "external_ref",
            unique=True,
        ),
        Index("ix_connector_connections_workspace_updated", "workspace_id", "updated_at", "id"),
        Index("ix_connector_connections_connector_status", "connector_provider_id", "status", "id"),
        Index("ix_connector_connections_owner", "workspace_id", "owner_type", "owner_id", "status", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    connector_provider_id: Mapped[str] = mapped_column(String(72), nullable=False)
    owner_type: Mapped[str | None] = mapped_column(String(32))
    owner_id: Mapped[str | None] = mapped_column(String(72))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False)
    connector_key: Mapped[str] = mapped_column(String(128), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(2048))
    safe_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    setup_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revoke_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(72), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def to_resource(self) -> ConnectorConnection:
        owner = None if self.owner_type is None or self.owner_id is None else _principal(self.owner_type, self.owner_id)
        return ConnectorConnection(
            id=self.id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
            connector_provider_id=self.connector_provider_id,
            owner_principal_ref=owner,
            name=self.name,
            connector_key=self.connector_key,
            safe_metadata=self.safe_metadata_json,
            status=ConnectorConnectionStatus(self.status),
            status_reason=(
                ConnectorConnectionStatusReason(self.status_reason) if self.status_reason is not None else None
            ),
            version=self.version,
            created_by=_principal(self.created_by_type, self.created_by_id),
            created_at=assume_utc(self.created_at),
            updated_at=assume_utc(self.updated_at),
        )


class ConnectorSetupAttemptRecord(Base):
    __tablename__ = "connector_setup_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ("connector_connection_id", "organization_id", "workspace_id"),
            ("connector_connections.id", "connector_connections.organization_id", "connector_connections.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('pending', 'attached', 'reserved', 'completed', 'failed', 'expired')",
            name="status_valid",
        ),
        CheckConstraint("generation >= 1", name="generation_positive"),
        CheckConstraint("initiating_principal_type = 'user'", name="initiating_user_required"),
        CheckConstraint("owner_type IS NULL OR owner_type IN ('user', 'service_account')", name="owner_type_valid"),
        CheckConstraint("(owner_type IS NULL) = (owner_id IS NULL)", name="owner_complete"),
        CheckConstraint("claim_generation >= 0", name="claim_generation_non_negative"),
        Index(
            "uq_connector_setup_attempts_connection_generation", "connector_connection_id", "generation", unique=True
        ),
        Index("ix_connector_setup_attempts_reconcile", "status", "available_at", "claim_expires_at", "id"),
        Index("ix_connector_setup_attempts_expiry", "expires_at", "status", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    connector_connection_id: Mapped[str] = mapped_column(String(72), nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    initiating_principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    initiating_principal_id: Mapped[str] = mapped_column(String(72), nullable=False)
    owner_type: Mapped[str | None] = mapped_column(String(32))
    owner_id: Mapped[str | None] = mapped_column(String(72))
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    connector_key: Mapped[str] = mapped_column(String(128), nullable=False)
    external_user_correlation: Mapped[str] = mapped_column(String(128), nullable=False)
    state_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    return_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    setup_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(2048))
    external_handle_digest: Mapped[str | None] = mapped_column(String(64))
    supports_verified_callback: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claim_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claim_owner: Mapped[str | None] = mapped_column(String(128))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConnectorOperationRecord(Base):
    __tablename__ = "connector_connection_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ("connector_connection_id", "organization_id", "workspace_id"),
            ("connector_connections.id", "connector_connections.organization_id", "connector_connections.workspace_id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("kind IN ('revoke')", name="kind_valid"),
        CheckConstraint("status IN ('pending', 'succeeded', 'unknown', 'failed')", name="status_valid"),
        CheckConstraint("generation >= 1", name="generation_positive"),
        CheckConstraint("claim_generation >= 0", name="claim_generation_valid"),
        Index(
            "uq_connector_connection_operations_generation",
            "connector_connection_id",
            "kind",
            "generation",
            unique=True,
        ),
        Index("ix_connector_connection_operations_reconcile", "status", "available_at", "claim_expires_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    connector_connection_id: Mapped[str] = mapped_column(String(72), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claim_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    claim_owner: Mapped[str | None] = mapped_column(String(128))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


def _principal(kind: str, identifier: str) -> PrincipalRef:
    return PrincipalRef(principal_type=PrincipalType(kind), principal_id=identifier)
