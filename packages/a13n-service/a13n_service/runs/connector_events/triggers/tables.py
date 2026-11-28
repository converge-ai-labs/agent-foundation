"""Subscription configuration and its fenced upstream operation."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, identity_guarded, rules


class TriggerRow(Stamped, Base):
    __tablename__ = "connector_triggers"
    KIND: ClassVar[str] = "connector_trigger"
    __table_args__ = (
        UniqueConstraint("workspace_id", "id"),
        UniqueConstraint("provider_id", "remote_id"),
        UniqueConstraint("workspace_id", "created_by_id", "request_key"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "provider_id"], ["connector_providers.workspace_id", "connector_providers.id"]
        ),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "connection_id"],
            ["connections.organization_id", "connections.workspace_id", "connections.id"],
        ),
        CheckConstraint("state IN ('enabled', 'paused', 'retired')", name="state"),
        Index(
            "uq_connector_triggers_subscription",
            "provider_id",
            "account_id",
            "event_type",
            "config_digest",
            unique=True,
            postgresql_where=text("state <> 'retired'"),
        ),
        Index("ix_connector_triggers_workspace", "workspace_id", "id"),
        Index("ix_connector_triggers_operation", "operation_due_at", postgresql_where=text("operation_id IS NOT NULL")),
        Index("ix_connector_triggers_binding", "binding_check_at", postgresql_where=text("state = 'enabled'")),
        rules(identity_guarded("connector_triggers"), unversioned=("binding_check_at",)),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str]
    workspace_id: Mapped[str]
    provider_id: Mapped[str]
    connection_id: Mapped[str]
    account_id: Mapped[str] = mapped_column(String(256))
    user_id: Mapped[str] = mapped_column(String(256))
    name: Mapped[str]
    event_type: Mapped[str] = mapped_column(String(128))
    definition: Mapped[dict] = mapped_column(JSONB)
    parameters: Mapped[dict] = mapped_column(JSONB)
    config_digest: Mapped[str] = mapped_column(String(64))
    target: Mapped[dict] = mapped_column(JSONB)
    initial_target: Mapped[dict] = mapped_column(JSONB)
    provisioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provisioning_stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    binding_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    state: Mapped[str]
    remote_id: Mapped[str | None] = mapped_column(String(256))
    operation_id: Mapped[str | None] = mapped_column(String(72))
    operation_kind: Mapped[str | None]
    operation_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    operation_attempts: Mapped[int] = mapped_column(default=0)
    failure: Mapped[str | None]
    request_key: Mapped[str]
    request_digest: Mapped[str] = mapped_column(String(64))
    created_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    updated_by_id: Mapped[str] = mapped_column(ForeignKey("principals.id"))
