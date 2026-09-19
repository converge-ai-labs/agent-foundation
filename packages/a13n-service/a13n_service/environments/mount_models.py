"""Accepted additional Run mounts and Attempt-fenced loading observations."""

from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base


class RunEnvironmentMountRecord(Base):
    __tablename__ = "run_environment_mounts"
    __table_args__ = (
        ForeignKeyConstraint(("organization_id", "run_id"), ("runs.organization_id", "runs.id"), ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ("workspace_id", "organization_id"),
            ("workspaces.id", "workspaces.organization_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("environment_id", "workspace_id"),
            ("environments.id", "environments.workspace_id"),
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("organization_id", "run_id", "applied_attempt_id"),
            ("run_attempts.organization_id", "run_attempts.run_id", "run_attempts.id"),
            ondelete="RESTRICT",
        ),
        CheckConstraint("name <> 'workspace' AND name ~ '^[a-z][a-z0-9-]{0,62}$'", name="name_valid"),
        CheckConstraint("principal_type IN ('user', 'service_account')", name="principal_type_valid"),
        CheckConstraint(
            "application_status IN ('pending', 'preparing', 'ready', 'failed')", name="application_status_valid"
        ),
        CheckConstraint(
            "(applied_attempt_id IS NULL AND applied_attempt_fence IS NULL AND observed_at IS NULL "
            "AND application_status = 'pending' AND error IS NULL) OR "
            "(applied_attempt_id IS NOT NULL AND applied_attempt_fence IS NOT NULL "
            "AND applied_attempt_fence > 0 AND observed_at IS NOT NULL)",
            name="observation_authority_valid",
        ),
        CheckConstraint("error IS NULL OR application_status = 'failed'", name="error_status_valid"),
        Index("ix_run_environment_mounts_acceptance", "run_id", "created_at", unique=True),
        Index("ix_run_environment_mounts_environment", "environment_id", "run_id"),
    )

    run_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    name: Mapped[str] = mapped_column(String(63), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(72), nullable=False)
    environment_id: Mapped[str] = mapped_column(String(72), nullable=False)
    use_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(72), nullable=False)
    applied_attempt_id: Mapped[str | None] = mapped_column(String(72))
    applied_attempt_fence: Mapped[int | None] = mapped_column(BigInteger)
    application_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON(none_as_null=True))
