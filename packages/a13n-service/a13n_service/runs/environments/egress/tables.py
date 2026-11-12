"""Nonsecret credential references for exact admitted Session incarnations."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from a13n_service.infra.db import Base


class SessionCredentialRow(Base):
    __tablename__ = "thread_session_credentials"
    __table_args__ = (
        PrimaryKeyConstraint("thread_id", "environment_id", "binding_name", "session_version", "env"),
        ForeignKeyConstraint(["organization_id", "workspace_id"], ["workspaces.organization_id", "workspaces.id"]),
        ForeignKeyConstraint(["workspace_id", "thread_id"], ["threads.workspace_id", "threads.id"]),
        ForeignKeyConstraint(["workspace_id", "environment_id"], ["environments.workspace_id", "environments.id"]),
        ForeignKeyConstraint(
            ["thread_id", "environment_id", "binding_name"],
            [
                "thread_session_bindings.thread_id",
                "thread_session_bindings.environment_id",
                "thread_session_bindings.name",
            ],
        ),
        ForeignKeyConstraint(
            ["workspace_id", "credential_id"], ["egress_credentials.workspace_id", "egress_credentials.id"]
        ),
        CheckConstraint("session_version > 0", name="session_version"),
        CheckConstraint("applied_credential_version > 0", name="credential_version"),
        CheckConstraint("applied_device_generation BETWEEN 1 AND 18446744073709551615", name="generation"),
        CheckConstraint(
            "num_nonnulls(applied_credential_version, applied_session_id, applied_device_generation, applied_at) IN (0, 4)",
            name="application",
        ),
        Index("ix_thread_session_credentials_credential", "credential_id"),
    )
    organization_id: Mapped[str] = mapped_column(String(72))
    workspace_id: Mapped[str] = mapped_column(String(72))
    thread_id: Mapped[str] = mapped_column(String(72))
    environment_id: Mapped[str] = mapped_column(String(72))
    binding_name: Mapped[str]
    session_version: Mapped[int] = mapped_column(BigInteger)
    env: Mapped[str]
    credential_id: Mapped[str] = mapped_column(String(72))
    inject_hosts: Mapped[list[str]] = mapped_column(JSONB)
    applied_credential_version: Mapped[int | None] = mapped_column(BigInteger)
    applied_session_id: Mapped[str | None]
    applied_device_generation: Mapped[Decimal | None] = mapped_column(Numeric(20, 0))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
