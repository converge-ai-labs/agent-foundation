"""Ownership and operation receipts for a13n-created Cloudflare gateways."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base, Stamped, rules


class DeploymentRow(Stamped, Base):
    __tablename__ = "cloudflare_deployments"
    KIND: ClassVar[str] = "cloudflare_deployment"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(
            ["workspace_id", "provider_id"], ["environment_providers.workspace_id", "environment_providers.id"]
        ),
        UniqueConstraint("provider_id"),
        UniqueConstraint("workspace_id", "request_id"),
        CheckConstraint("status IN ('deploying', 'ready', 'retiring', 'needs_attention', 'retired')", name="status"),
        CheckConstraint("operation IN ('deploy', 'retire')", name="operation"),
        CheckConstraint("(claim IS NULL) = (claim_until IS NULL)", name="claim"),
        CheckConstraint("(status = 'retired') = (deployment_token IS NULL AND gateway_token IS NULL)", name="secrets"),
        Index("ix_cloudflare_deployments_due", "due_at", postgresql_where=text("status IN ('deploying', 'retiring')")),
        rules(unversioned=("claim", "claim_until", "due_at", "attempts")),
    )
    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str]
    workspace_id: Mapped[str]
    request_id: Mapped[str] = mapped_column(String(36))
    provider_id: Mapped[str | None] = mapped_column(String(72))
    account_id: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128))
    worker_name: Mapped[str] = mapped_column(String(63))
    source_image: Mapped[str]
    service_origin: Mapped[str | None]
    deployment_token: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    gateway_token: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    status: Mapped[str]
    operation: Mapped[str]
    stage: Mapped[str]
    resources: Mapped[dict] = mapped_column(JSONB)
    error: Mapped[str | None]
    attempts: Mapped[int] = mapped_column(default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claim: Mapped[str | None] = mapped_column(String(72))
    claim_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
