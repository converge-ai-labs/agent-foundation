"""Relational facts shared across Connectivity resource families."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.database import Base


class ConnectivityCommandRecord(Base):
    """Bounded idempotency evidence for one committed Connectivity mutation."""

    __tablename__ = "connectivity_commands"
    __table_args__ = (
        CheckConstraint("result_version >= 1", name="result_version_positive"),
        CheckConstraint("actor_type IN ('user', 'service_account')", name="actor_type_valid"),
        Index(
            "uq_connectivity_commands_request",
            "actor_type",
            "actor_id",
            "operation",
            "scope_id",
            "idempotency_key_digest",
            unique=True,
        ),
        Index("ix_connectivity_commands_resource", "resource_type", "resource_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(72), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(72))
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(72), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(72), nullable=False)
    idempotency_key_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(72), nullable=False)
    result_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
