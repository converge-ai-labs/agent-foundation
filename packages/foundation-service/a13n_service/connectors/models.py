"""Relational authority for Connector domain resources."""

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
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base


class ConnectorRecord(Base):
    """Stable Workspace Connector metadata and lifecycle authority."""

    __tablename__ = "connectors"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_connectors_workspace_updated", "organization_id", "workspace_id", "updated_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConnectorRevisionRecord(Base):
    """One immutable Connector Provider configuration revision."""

    __tablename__ = "connector_revisions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint("connector_id", "version", name="uq_connector_revisions_connector_version"),
        Index(
            "ix_connector_revisions_workspace_connector",
            "organization_id",
            "workspace_id",
            "connector_id",
            "version",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    connector_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("connectors.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_key: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_config_version: Mapped[str] = mapped_column(String(200), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConnectionRecord(Base):
    """One external account authorization for a Connector."""

    __tablename__ = "connections"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "status IN ('active', 'disabled', 'reauthorization_required', 'revoked')",
            name="status_valid",
        ),
        CheckConstraint(
            "(principal_type IS NULL AND principal_id IS NULL) OR "
            "(principal_type IN ('user', 'service_account') AND principal_id IS NOT NULL)",
            name="principal_ref_complete",
        ),
        Index(
            "ix_connections_workspace_connector_status",
            "organization_id",
            "workspace_id",
            "connector_id",
            "status",
            "id",
        ),
        Index(
            "ix_connections_personal_selection",
            "organization_id",
            "workspace_id",
            "connector_id",
            "principal_type",
            "principal_id",
            "status",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    connector_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("connectors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    principal_type: Mapped[str | None] = mapped_column(String(32))
    principal_id: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_state_version: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    account_external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    account_display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lifecycle_operation_id: Mapped[str | None] = mapped_column(String(200))
    cleanup_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConnectionSetupRecord(Base):
    """Expiring encrypted Provider setup state; not a public product resource."""

    __tablename__ = "connection_setups"
    __table_args__ = (
        CheckConstraint(
            "status IN ('starting', 'pending', 'completing', 'completed', 'failed')",
            name="status_valid",
        ),
        UniqueConstraint("operation_id", name="uq_connection_setups_operation_id"),
        Index("ix_connection_setups_expiry", "status", "expires_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    connector_revision_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("connector_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_connection_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("connections.id", ondelete="RESTRICT"),
    )
    provider_key: Mapped[str] = mapped_column(String(200), nullable=False)
    principal_type: Mapped[str | None] = mapped_column(String(32))
    principal_id: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    setup_mode: Mapped[str] = mapped_column(String(100), nullable=False)
    initiated_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    initiated_by_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    continuation_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    next_action: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(200))
    connection_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("connections.id", ondelete="RESTRICT"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TriggerRecord(Base):
    """Mutable schedule or Connector-event source targeting one Agent revision."""

    __tablename__ = "triggers"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "status IN ('disabled', 'activating', 'active', 'failed')",
            name="status_valid",
        ),
        CheckConstraint(
            "principal_type IN ('user', 'service_account')",
            name="principal_type_valid",
        ),
        CheckConstraint(
            "source_kind IN ('schedule', 'connector_event')",
            name="source_kind_valid",
        ),
        CheckConstraint(
            "(source_kind = 'schedule' AND connector_revision_id IS NULL AND connection_id IS NULL "
            "AND event_type IS NULL AND provider_event_config_version IS NULL AND event_config IS NULL) OR "
            "(source_kind = 'connector_event' AND schedule_type IS NULL AND schedule_expression IS NULL "
            "AND schedule_timezone IS NULL AND interval_seconds IS NULL "
            "AND connector_revision_id IS NOT NULL AND connection_id IS NOT NULL "
            "AND event_type IS NOT NULL AND provider_event_config_version IS NOT NULL "
            "AND event_config IS NOT NULL)",
            name="source_fields_match_kind",
        ),
        CheckConstraint(
            "source_kind != 'schedule' OR "
            "(schedule_type = 'cron' AND schedule_expression IS NOT NULL "
            "AND schedule_timezone IS NOT NULL AND interval_seconds IS NULL) OR "
            "(schedule_type = 'interval' AND schedule_expression IS NULL "
            "AND schedule_timezone IS NULL AND interval_seconds > 0)",
            name="schedule_fields_valid",
        ),
        CheckConstraint(
            "(provider_state_version IS NULL AND provider_state IS NULL) OR "
            "(provider_state_version IS NOT NULL AND provider_state IS NOT NULL)",
            name="provider_state_complete",
        ),
        Index("ix_triggers_workspace_updated", "organization_id", "workspace_id", "updated_at", "id"),
        Index("ix_triggers_schedule_claim", "status", "source_kind", "next_scheduled_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_revision_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    schedule_type: Mapped[str | None] = mapped_column(String(32))
    schedule_expression: Mapped[str | None] = mapped_column(String(200))
    schedule_timezone: Mapped[str | None] = mapped_column(String(200))
    interval_seconds: Mapped[int | None] = mapped_column(BigInteger)
    next_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connector_revision_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("connector_revisions.id", ondelete="RESTRICT"),
    )
    connection_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("connections.id", ondelete="RESTRICT"),
    )
    event_type: Mapped[str | None] = mapped_column(String(200))
    provider_event_config_version: Mapped[str | None] = mapped_column(String(200))
    event_config: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    input_template: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provider_state_version: Mapped[str | None] = mapped_column(String(200))
    provider_state: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    event_cursor: Mapped[str | None] = mapped_column(String(2_000))
    lifecycle_operation_id: Mapped[str | None] = mapped_column(String(200))
    cleanup_pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TriggerOccurrenceRecord(Base):
    """Internal occurrence deduplication and accepted-work correlation."""

    __tablename__ = "trigger_occurrences"
    __table_args__ = (
        CheckConstraint(
            "(provider_event_id IS NOT NULL AND scheduled_for IS NULL) OR "
            "(provider_event_id IS NULL AND scheduled_for IS NOT NULL)",
            name="source_identity_valid",
        ),
        UniqueConstraint("trigger_id", "occurrence_key", name="uq_trigger_occurrences_trigger_key"),
        Index(
            "ix_trigger_occurrences_workspace_received",
            "organization_id",
            "workspace_id",
            "received_at",
            "id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("triggers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    occurrence_key: Mapped[str] = mapped_column(String(500), nullable=False)
    provider_event_id: Mapped[str | None] = mapped_column(String(500))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_turn_id: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
